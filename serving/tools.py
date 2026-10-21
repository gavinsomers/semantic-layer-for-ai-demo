"""The four tools an assistant may call, each with the caller's identity.

Guide chapters 4 and 7. The MCP server (mcp_server.py) and the agent
(agent.py) both call these functions; neither adds rules of its own.

    list_metrics(user)                       what may be asked for
    get_dimensions(user, metric)             how it may be broken down
    query_metrics(user, metric, ...)         governed answer -> response contract
    run_readonly_sql(user, sql, ...)         flagged fallback -> response contract

Every call is checked against serving/users.yml and written to the audit log.
"""

import csv
from datetime import date
from decimal import Decimal

from serving import access, clock, contract, layer, unusual
from serving.layer import DBT_DIR, LayerError
from serving.sql_guard import SqlRejected
from serving import sql_guard

DEFAULT_CUTOFF = "september_close"


def cutoffs():
    """Named cutoffs as instants in UK time with their offset:
    {"friday_review": "2026-09-04T06:00:00+01:00", ...}. The seed gives each
    one as a UK clock time and its zone, as the warehouse's stg_cutoffs does."""
    with (DBT_DIR / "seeds" / "cutoffs.csv").open(encoding="utf-8") as f:
        return {row["cutoff_id"]: clock.local(row["cutoff_local"], row["time_zone"]).isoformat()
                for row in csv.DictReader(f)}


def list_metrics(user):
    """Metrics this user may ask for, with descriptions and operating agreement."""
    policy = access.user(user)
    visible = []
    for metric in layer.metrics().values():
        meta = metric["meta"]
        if meta.get("hidden") or metric["name"] in policy["deny_metrics"]:
            continue
        if not _row_filters_fit(policy, metric["name"]):
            continue
        visible.append({"name": metric["name"], "label": metric["label"],
                        "description": metric["description"], "type": metric["type"],
                        **{k: meta[k] for k in ("owner", "status", "version", "unit") if k in meta},
                        **({"effective_from": str(meta["effective_from"]),
                            "in_force_today": date.fromisoformat(str(meta["effective_from"]))
                            <= clock.today()} if meta.get("effective_from") else {})})
    access.audit(user=user, tool="list_metrics", returned=len(visible))
    return visible


def get_dimensions(user, metric):
    """Dimensions and entities for one metric (via `mf list`), plus the cutoffs."""
    policy = access.user(user)
    try:
        access.check_metric(policy, metric)
        if metric not in layer.metrics():
            raise LayerError(f"'{metric}' is not a governed metric.")
        result = layer.list_dimensions(metric)
    except (access.AccessDenied, LayerError) as error:
        access.audit(user=user, tool="get_dimensions", metric=metric, error=str(error),
                     access_denied=isinstance(error, access.AccessDenied))
        return {"error": str(error)}
    result["not_supported"] = layer.metrics()[metric]["meta"].get("cannot_group_by", [])
    result["cutoffs"] = cutoffs()
    access.audit(user=user, tool="get_dimensions", metric=metric)
    return result


def query_metrics(user, metric, group_by=None, filters=None, start=None, end=None,
                  cutoff=None, question="", category="supported", caveats=(), reproduce=False,
                  unfiltered=(), keep_version=False, bound_filters=None):
    """Ask the semantic layer for one metric. Returns a response contract.

    Settings are settled in the book's order: what the question says, then the
    definition's defaults, then the access policy, which always wins.
    reproduce=True: the question asks to reproduce a past report (the router
    decides), which a hold does not stop. unfiltered: dimensions the question
    scoped wider ("across all channels"), so no default filter narrows them.
    keep_version=True: the question names a past report or the old rule, so a
    superseded version answers as asked; otherwise the version in force does.
    bound_filters: filters the question itself pins down (the router decides,
    router.bound_filters); a request that leaves one out gets it applied, with
    a caveat, so "how many sofas" never gets the all-product figure.
    """
    group_by, filters = list(group_by or []), dict(filters or {})
    for key, value in (bound_filters or {}).items():
        if key not in filters:
            filters[key] = value
            caveats = [f"The question asks for {key.replace('_', ' ')} = {value}; the request "
                       "left it out, so it was applied.", *caveats]
    known = layer.metrics()
    rows_limited = {}
    version_note = []

    # 0. Version binding (guide ch 9). Which version answers a current question
    #    is the layer's decision, not the caller's: a model reading stale
    #    instructions may still name the old version. Once a successor is in
    #    force on the demo clock's date, it answers, and a caveat says so.
    successor = _successor_in_force(metric) if metric in known else None
    if successor and not (reproduce or keep_version):
        old = known[metric]["label"]
        metric = successor[0]
        version_note.append(f"You asked for {old}. From {clock.long_date(successor[1])} "
                            f"{known[metric]['label']} is the rule for current answers, so this "
                            "answer uses it. To reproduce a report made under the old rule, name "
                            "the report.")
    definition = known.get(metric)

    def refuse(reason, category="unsupported", denied=False):
        result = contract.refused(question, user, reason, category, metrics=[metric],
                                  access={"row_filters": rows_limited, "denied": denied})
        access.audit(user=user, tool="query_metrics", question=question, path="refused",
                     metric=metric, category=category, reason=reason, access_denied=denied)
        return contract.validate(result)

    # 1. Is it a governed metric, and may this person use it?
    if definition is None or definition["meta"].get("hidden"):
        return refuse(f"'{metric}' is not a governed metric.")
    meta = definition["meta"]
    try:
        policy = access.user(user)
        access.check_metric(policy, metric)
    except access.AccessDenied as denied:
        return refuse(str(denied), "constrained", denied=True)
    rows_limited = access.row_filters(policy)

    # 1b. On hold (guide ch 9; book ch 8: when a new rule took effect 38 hours
    #     before its checked version shipped, current contribution was on hold).
    #     The reason goes back instead of a figure; past reports still reproduce.
    hold = hold_in_force(meta)
    if hold and not reproduce:
        return refuse(f"{definition['label']} is on hold until {hold['until']}: "
                      f"{' '.join(str(hold['reason']).split())} A report already made with it "
                      "can still be reproduced: name the report.", "constrained")

    # 2. Breakdowns the definition does not support: refuse that part, with the reason.
    blocked = [g for g in group_by + list(filters) if g in meta.get("cannot_group_by", [])]
    if blocked:
        return refuse(f"{definition['label']} cannot be split by {', '.join(blocked)}: "
                      + _split_reason(metric), "constrained")
    # The names MetricFlow uses for this metric ("product" is
    # stock_snapshot__stock_product for stock, order_line_snapshot__product for
    # order lines). A dimension the metric does not have is refused, not dropped:
    # dropping it would answer a wider question.
    resolved = {name: layer.resolve_dimension(metric, name) for name in group_by + list(filters)}
    missing = [name for name, found in resolved.items() if found is None]
    if missing:
        return refuse(f"{definition['label']} cannot be broken down or filtered by "
                      f"{', '.join(missing)}.", "constrained")

    # 3. Settle the request: question, then the definition's defaults.
    applied = [{"field": k, "value": v, "source": "question"} for k, v in filters.items()]
    defaults = meta.get("defaults", {})
    for key, value in defaults.get("filters", {}).items():
        if key not in filters and key not in unfiltered:
            filters[key] = value
            applied.append({"field": key, "value": value, "source": "default"})
    start = start or defaults.get("start")
    end = end or defaults.get("end")
    cutoff = cutoff or defaults.get("cutoff") or DEFAULT_CUTOFF
    if cutoff not in cutoffs():
        return refuse(f"There is no cutoff '{cutoff}'. Known: {', '.join(cutoffs())}.", "constrained")

    # 4. The access policy's row filters. Applied in the query, never in a prompt.
    if rows_limited and not _row_filters_fit(policy, metric):
        return refuse(f"Your access is limited to {_describe(rows_limited)} rows, and "
                      f"{definition['label']} cannot be limited that way.", "constrained", denied=True)
    for key, value in rows_limited.items():
        if filters.get(key, value) != value:
            return refuse(f"Your access is limited to {_describe(rows_limited)} rows.",
                          "constrained", denied=True)
        if key not in filters:
            applied.append({"field": key, "value": value, "source": "access_policy"})
        filters[key] = value

    # 5. Run it: the rows, the compiled SQL and a completeness check.
    gap = advertising_gap(metric, filters, start, end, cutoff)
    if gap:                     # the window cuts through an advertising period
        result = contract.clarify(question, user, gap["question"], gap["options"], "constrained")
        access.audit(user=user, tool="query_metrics", question=question, path="clarify",
                     metric=metric, category="constrained", reason=gap["question"])
        return contract.validate(result)
    where = [layer.where_clause("cutoff", cutoff)]
    where += [layer.where_clause(resolved.get(k, k), v) for k, v in filters.items()]
    # Contribution also asks for lines_with_unknown_cost, in the same request.
    asked = [metric] + (["lines_with_unknown_cost"] if metric.startswith("contribution") else [])
    try:
        result = layer.query(asked, [resolved[g] for g in group_by], where, start, end,
                             with_total=definition["type"] != "cumulative")
    except LayerError as error:
        return refuse(f"the semantic layer could not run this request: {error}", "constrained")
    # Rows keyed by the names asked for: MetricFlow may name a column
    # differently (a qualified name, or a time dimension with its grain added).
    columns = {g: layer.column_for(result["rows"], resolved[g]) for g in group_by}
    result["rows"] = [{next((g for g, c in columns.items() if c == k), k): v for k, v in row.items()}
                      for row in result["rows"]]

    total = (result["total"] or {}).get(metric)
    unknown = (result["total"] or {}).get("lines_with_unknown_cost") or 0
    if unknown:
        return refuse(f"{unknown} order line(s) in this selection have an unknown cost. "
                      "An unknown cost is never treated as zero, so contribution cannot "
                      "be calculated for this selection.", "constrained")

    answer = contract.new(
        question, user, category, "governed",
        metrics=[metric], values=[{k: v for k, v in row.items() if k != "lines_with_unknown_cost"}
                                  for row in result["rows"]],
        total=total, filters_applied=applied,
        access={"row_filters": rows_limited, "denied": False},
        cutoff={"id": cutoff, "at": cutoffs()[cutoff]},
        time_window={"start": start, "end": end} if (start or end) else None,
        caveats=list(dict.fromkeys(version_note + list(caveats)
                                   + _caveats(definition, result["rows"], metric,
                                                            rows_limited)
                                   + _unusual(definition, filters, total, start, end))),  # once each
        lineage=[contract.lineage_entry(definition)], compiled_sql=result["sql"],
        answer_text=summarise(definition, total, result["rows"], group_by))
    access.audit(user=user, tool="query_metrics", question=question, path="governed",
                 metric=metric, version=meta.get("version"), status=meta.get("status"),
                 category=category, filters=filters, cutoff=cutoff, command=result["command"])
    return contract.validate(answer)


def fallback_blocked(route, governed, earlier=()):
    """Why the SQL fallback may not run yet, or None if it may.

    One rule for every caller (the agent loop and the MCP server): the fallback
    never widens an access refusal, and it waits while a governed metric fits
    and has not failed. `governed` holds only query_metrics results; `earlier`
    any other contracts. A refused fallback is not a failed governed attempt.

    What this can and cannot enforce: `route` is the router's reading of the
    `question` the caller sends. The agent loop sends the user's own words; an
    MCP client sends whatever its model writes, and a model that wants raw SQL
    can misstate the question. So this rule steers an honest client and
    records an attempted bypass. Only the warehouse role behind the fallback
    (read-only, the user's rows and allowed columns) truly enforces the order.
    This demo has no warehouse roles: its stand-ins are the read-only DuckDB
    connection and the SQL guard, which hold whatever question is sent.
    """
    if any((c.get("access") or {}).get("denied") for c in [*governed, *earlier]):
        return "Access was refused for this question. The SQL fallback cannot widen access."
    if route.path == "refused":
        return route.reason
    governed_failed = any(c["path"] == "refused" for c in governed)
    if route.path == "governed" and not governed_failed:
        metric = route.request.get("metric")
        return f"A governed metric fits this question ({metric}): call query_metrics first."
    return None


def qualifier_blocked(route, metric):
    """Why a governed query may not answer this question, or None.

    A question that narrows the selection ("orders that used a promotion")
    must not get a governed figure that ignores the narrowing: that answers a
    wider question with a governed label on it. Same caveat as above: the
    route comes from the `question` the caller sends.
    """
    rule = route.unapplied if route else None
    if rule and metric not in rule["carried_by"]:
        return (f"The question asks only for {rule['meaning']}, and {metric} cannot be limited "
                "that way, so a governed figure would answer a wider question.")
    return None


def run_readonly_sql(user, sql, cutoff=None, question="", category="constrained"):
    """The fallback: guarded, read-only SQL, always flagged in the contract."""
    cutoff = cutoff or DEFAULT_CUTOFF
    policy = access.user(user)
    try:
        if cutoff not in cutoffs():
            raise SqlRejected(f"There is no cutoff '{cutoff}'.")
        safe_sql = sql_guard.guard(sql, access.sql_policy(policy), cutoff)
        rows = [{k: _plain(v) for k, v in row.items()} for row in sql_guard.run(safe_sql)]
    except (SqlRejected, Exception) as error:
        reason = str(error) if isinstance(error, SqlRejected) else f"the SQL failed: {error}"
        denied = isinstance(error, sql_guard.SqlAccessDenied)
        access.audit(user=user, tool="run_readonly_sql", question=question, path="refused",
                     category=category, reason=reason, sql=sql, access_denied=denied)
        return contract.validate(contract.refused(
            question, user, reason, category,
            access={"row_filters": access.row_filters(policy), "denied": denied}))

    filters = [{"field": k, "value": v, "source": "access_policy"}
               for k, v in access.row_filters(policy).items()]
    answer = contract.new(
        question, user, category, "sql_fallback", values=rows, filters_applied=filters,
        cutoff={"id": cutoff, "at": cutoffs()[cutoff]}, compiled_sql=safe_sql,
        access={"row_filters": access.row_filters(policy), "denied": False},
        caveats=["Not a governed definition: read-only SQL, checked by the SQL guard "
                 "but not reviewed by a metric owner. Treat it as provisional."],
        total=_single_value(rows), answer_text=_describe_rows(rows))
    access.audit(user=user, tool="run_readonly_sql", question=question, path="sql_fallback",
                 category=category, cutoff=cutoff, sql=safe_sql)
    return contract.validate(answer)


# --- Helpers ----------------------------------------------------------------

def summarise(definition, total, rows, group_by):
    """A plain answer from the figures. The model may reword it, not change it."""
    unit = definition["meta"].get("unit")
    text = definition["label"]
    if not rows:
        return text + ": no records match this selection."
    if total is not None or not group_by:
        text += f": {money(total, unit)}"
    if group_by:
        split = "; ".join(f"{' / '.join(str(row.get(g)) for g in group_by)} "
                          f"{money(row.get(definition['name']), unit)}"
                          for row in rows[:12])
        text += f" ({split}{'; ...' if len(rows) > 12 else ''})"
    return text + "."


def money(value, unit="GBP"):
    """50 -> '£50', -70 -> '−£70', 0.0667 -> '0.0667', None -> 'unknown'."""
    if value is None:
        return "unknown"
    if unit != "GBP":
        return f"{value:,g}" + ("" if unit in (None, "fraction") else f" {unit}")
    sign = "−" if value < 0 else ""
    amount = f"{abs(value):,.2f}".removesuffix(".00")
    return f"{sign}£{amount}"


def _row_filters_fit(policy, metric):
    """Can every row filter this user carries be applied to this metric?"""
    return set(access.row_filters(policy)) <= layer.metric_entities(metric)


def _caveats(definition, rows, metric, rows_limited):
    meta, notes = definition["meta"], []
    if meta["status"] == "draft":
        notes.append(f"{definition['label']} is a draft (version {meta['version']}), "
                     "not yet certified by its owner.")
    if meta["status"] == "deprecated":
        # Guide ch 9: the deprecation notice lives in the metric's meta, so every
        # consumer (this agent, a BI tool) passes on the same words.
        notice = " ".join(str((meta.get("deprecation") or {}).get("notice", "")).split())
        notes.append(f"Deprecated: {notice}" if notice else
                     f"{definition['label']} is deprecated.")
    if meta.get("effective_from"):
        # Version status on the demo clock's date (serving/clock.py), not the computer's.
        start = date.fromisoformat(str(meta["effective_from"]))
        if clock.today() < start:
            notes.append(f"A comparison, not the rule in force: {_rule_in_force(metric)} is in "
                         f"force until {start}. Asking does not change the rule.")
        else:
            note = (f"Version {meta['version']} is the rule for current answers from "
                    f"{clock.long_date(start)} (demo clock: {clock.long_date(clock.today())}).")
            if meta["status"] == "draft":     # only a draft needs the explanation
                note += (" This project records the definitions of 2 October 2026, where it "
                         "is still marked draft.")
            notes.append(note)
    if hold_in_force(meta):
        notes.append(f"On hold for current answers until {meta['hold']['until']}. This "
                     "reproduces a report already made with it.")
    successor = _successor_in_force(metric)
    if successor:
        notes.append(f"{definition['label']} was the rule in force until "
                     f"{clock.long_date(successor[1])}; current answers use {successor[0]}. "
                     "This answer reproduces reports made under it.")
    if metric.startswith("contribution"):
        notes.append("Provisional contribution: neither cash received nor final profit. "
                     "Overheads and payment-provider fees are left out; costs are estimates.")
    if any(row.get(metric) is None for row in rows):
        notes.append("Some values are empty because a required input is missing (for example "
                     "advertising for that channel). A missing input is never treated as zero.")
    if rows_limited:
        notes.append(f"Limited by your access to {_describe(rows_limited)} rows. "
                     "This is not the business-wide total.")
    if meta.get("unit") == "units":
        notes.append("Stock is a position on the last day of the period, not a sum over days. "
                     "Do not use an observation more than 24 hours old for a live allocation.")
    return notes


def _unusual(definition, filters, total, start, end):
    """A caveat if the figure is far outside the metric's daily history (serving/unusual.py)."""
    found = unusual.check(definition["name"], filters, total, start, end)
    if not found:
        return []
    unit, per = definition["meta"].get("unit"), " a day" if found["per_day"] else ""
    return [f"Unusual value: {money(found['value'], unit)}{per} is outside the usual range for "
            f"{definition['label']} ({money(found['low'], unit)} to {money(found['high'], unit)}"
            f"{per}, from {found['days']} days at the {found['cutoff']} cutoff). Check the data "
            "before relying on it."]


def hold_in_force(meta):
    """The metric's hold, if its status is on_hold and the demo clock's date is
    within it (meta hold: since, until, reason; dates, as the clock is a date)."""
    hold = meta.get("hold") or {}
    if meta.get("status") != "on_hold":
        return None
    since = date.fromisoformat(str(hold.get("since", "0001-01-01"))[:10])
    until = date.fromisoformat(str(hold.get("until", "9999-12-31"))[:10])
    return hold if since <= clock.today() <= until else None


def _successor_in_force(metric):
    """(successor, its start date) if a later version replaces this metric on the
    demo clock's date (context/ambiguity.yml `versions`), else None."""
    from serving import router
    for rule in router.load_context()["ambiguity"].get("versions") or []:
        if rule["metric"] == metric and router.in_force(rule["successor"]):
            start = layer.metrics()[rule["successor"]]["meta"]["effective_from"]
            return rule["successor"], date.fromisoformat(str(start))
    return None


def advertising_gap(metric, filters, start, end, cutoff):
    """A named-choice question if the window cuts through an advertising period.

    Advertising is recorded for whole periods (31 August to 3 September for
    the trial) and nobody has agreed a rule for splitting one by day. A window
    that holds only part of a period would get none of its advertising (or all
    of it), so a metric that deducts advertising cannot answer it: ask which
    whole-period window is meant instead. None if the window is whole, or the
    metric has no advertising input, or no window was given.
    """
    if not (start and end) or metric not in layer.metrics():
        return None
    if not any(leaf["semantic_model"] == "advertising"
               for leaf in layer._leaves(metric, layer.manifest())):
        return None
    where = [layer.where_clause("cutoff", cutoff)]
    where += [layer.where_clause(k, v) for k, v in filters.items() if k in ("channel", "product_group")]
    period = ["ad_spend_snapshot__period_start", "ad_spend_snapshot__period_end"]
    try:
        rows = layer.query(["advertising"], period, where, with_total=False)["rows"]
    except LayerError:
        return None
    first, last = date.fromisoformat(str(start)[:10]), date.fromisoformat(str(end)[:10])
    cut = []
    for row in rows:
        p_start, p_end = (date.fromisoformat(str(row[layer.column_for(rows, p)])[:10]) for p in period)
        if p_start <= last and p_end >= first and not (first <= p_start and p_end <= last):
            cut.append((p_start, p_end))
    if not cut:
        return None
    try:                        # an unknown cost refuses the selection whatever the window
        unknown = layer.query(["lines_with_unknown_cost"], (), where, str(start)[:10],
                              str(end)[:10])["rows"]
        if unknown and (unknown[0].get("lines_with_unknown_cost") or 0) > 0:
            return None
    except LayerError:
        pass
    p_start, p_end = min(cut)
    wide = (min([first] + [s for s, _ in cut]), max([last] + [e for _, e in cut]))
    day = lambda d: f"{d.day} {d:%B %Y}"
    label = layer.metrics()[metric]["label"]
    question = (f"Advertising is recorded for whole periods and is never split by day, and the "
                f"record for {day(p_start)} to {day(p_end)} only partly falls in {day(first)} to "
                f"{day(last)}, so {label} cannot be given for that window. Do you want it for "
                f"{day(p_start)} to {day(p_end)} (the {_days(p_start, p_end)} the advertising record "
                f"covers) or for {day(wide[0])} to {day(wide[1])} (your window widened to whole "
                "advertising periods)?")
    options = [f"{metric} for {day(p_start)} to {day(p_end)}, the {_days(p_start, p_end)} the "
               "advertising record covers",
               f"{metric} for {day(wide[0])} to {day(wide[1])}, widened to whole advertising periods"]
    return {"question": question, "options": options,
            "windows": [(p_start.isoformat(), p_end.isoformat()),
                        (wide[0].isoformat(), wide[1].isoformat())]}


def _days(first, last):
    n = (last - first).days + 1
    words = {1: "one day", 2: "two days", 3: "three days", 4: "four days", 5: "five days",
             6: "six days", 7: "seven days"}
    return words.get(n, f"{n} days")


def _rule_in_force(metric):
    """How to name the version a not-yet-in-force metric will replace, from its
    metadata: 'version 1 (paid-refund rule)'. Generated, so it never goes stale."""
    from serving import router
    for rule in router.load_context()["ambiguity"].get("versions") or []:
        if rule["successor"] == metric and rule["metric"] in layer.metrics():
            meta = layer.metrics()[rule["metric"]]["meta"]
            return f"version {meta['version']}" + (f" ({meta['refund_rule']})"
                                                   if meta.get("refund_rule") else "")
    return "the current version"


def version_status():
    """One line per versioned metric: which version answers current questions on
    the demo clock's date, generated from metric metadata (effective_from) and
    context/ambiguity.yml `versions`, so no hand-written file has to say it."""
    from serving import router
    known, lines = layer.metrics(), []
    for rule in router.load_context()["ambiguity"].get("versions") or []:
        if rule["metric"] not in known or rule["successor"] not in known:
            continue
        old, new = known[rule["metric"]], known[rule["successor"]]
        start = date.fromisoformat(str(new["meta"]["effective_from"]))
        if router.in_force(rule["successor"]):
            lines.append(f"{new['name']} ({new['label']}) answers current questions, since "
                         f"{clock.long_date(start)}; {old['name']} only reproduces reports "
                         "made under it, such as the Friday review.")
        else:
            lines.append(f"{old['name']} ({old['label']}) is the rule in force; "
                         f"{new['name']} takes over on {clock.long_date(start)}. Until then use "
                         f"{new['name']} only as a labelled comparison.")
    return lines


def _split_reason(metric):
    return ("Payments belong to whole orders, and nobody has agreed how to split them."
            if metric == "cash_received" else
            "Advertising is recorded for whole product groups, and nobody has agreed "
            "a rule for sharing it out.")


def _describe(filters):
    return ", ".join(f"{k.replace('_', ' ')} = {v}" for k, v in filters.items())


def _single_value(rows):
    """The value of a one-row, one-column result, else None."""
    if len(rows) == 1 and len(rows[0]) == 1:
        value = next(iter(rows[0].values()))
        return value if isinstance(value, (int, float)) else None
    return None


def _describe_rows(rows):
    if _single_value(rows) is not None:
        return f"{next(iter(rows[0]))}: {_single_value(rows):,g}."
    return f"{len(rows)} row(s) returned."


def _plain(value):
    """DuckDB values to JSON-friendly ones."""
    if isinstance(value, Decimal):
        return int(value) if value == int(value) else float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
