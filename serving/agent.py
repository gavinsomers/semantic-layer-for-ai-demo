"""The governed assistant: route first, governed metric next, SQL last.

Guide chapter 4. For each question:

  1. The router classifies it (no model involved).
       unsupported -> refuse now; nothing is queried.
       constrained and ambiguous -> ask one named-choice question; nothing runs.
  2. Otherwise the model answers with four tools, as the signed-in user:
       list_metrics, get_dimensions, query_metrics, run_readonly_sql.
     run_readonly_sql is refused while a governed metric fits the question.
  3. Code, not the model, decides what goes back: the response contract from
     the tool, with the model's wording only if every number in it came
     from the contract.

offline=True replaces step 2 with the router's own request (or a verified
SQL query), so tests and the quick start need no model server.
"""

import json
import re
import time
from pathlib import Path

from serving import access, contract, router, tools

INSTRUCTIONS = (Path(__file__).resolve().parent.parent / "context" / "instructions.md")

TOOL_SPECS = [
    {"type": "function", "function": {
        "name": "list_metrics",
        "description": "List the governed metrics you may use: name, label, description, owner, status, version, unit.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_dimensions",
        "description": "Dimensions and entities one metric can be grouped or filtered by, what it cannot be split by, and the named cutoffs.",
        "parameters": {"type": "object", "properties": {"metric": {"type": "string"}},
                       "required": ["metric"]}}},
    {"type": "function", "function": {
        "name": "query_metrics",
        "description": "Ask the semantic layer for one governed metric. Returns a response contract: "
                       "values, total, filters applied, cutoff, caveats, lineage and compiled SQL, or a refusal with the reason.",
        "parameters": {"type": "object", "properties": {
            "metric": {"type": "string"},
            "group_by": {"type": "array", "items": {"type": "string"},
                         "description": "e.g. [\"product_group\"], [\"channel\"], [\"metric_time__month\"]"},
            "filters": {"type": "object", "description": "Equality filters, e.g. {\"channel\": \"paid_search\"}"},
            "start": {"type": "string", "description": "First day, YYYY-MM-DD, inclusive"},
            "end": {"type": "string", "description": "Last day, YYYY-MM-DD, inclusive"},
            "cutoff": {"type": "string", "description": "friday_review, lamp_recalculation or september_close"}},
            "required": ["metric"]}}},
    {"type": "function", "function": {
        "name": "run_readonly_sql",
        "description": "FALLBACK ONLY, when no governed metric fits. One read-only DuckDB SELECT over "
                       "fct_order_lines, fct_ad_spend, fct_cash_movements, fct_stock_daily, dim_customers. "
                       "Rows are already limited to one cutoff and to what you may see. The answer is flagged as not governed.",
        "parameters": {"type": "object", "properties": {
            "sql": {"type": "string"}, "cutoff": {"type": "string"}}, "required": ["sql"]}}},
]


def ask(question, user, offline=False, allow_sql=True, stats=None):
    """Answer one question for one user. Returns (contract, tool calls).

    allow_sql=False removes the SQL fallback (the evaluation's "governed" variant).
    stats, if given, collects the model's token counts. Every answer is also
    written to the telemetry log (telemetry/log.py), one line per answer.
    """
    from telemetry import log
    started, stats = time.perf_counter(), ({} if stats is None else stats)
    request = access.start_request("offline" if offline else None)   # live: set once known
    result, calls = _ask(question, user, offline, allow_sql, stats)
    log.write(log.record_for(result, (time.perf_counter() - started) * 1000, stats,
                             request_id=request["request_id"]))
    return result, calls


def _ask(question, user, offline, allow_sql, stats):
    access.user(user)                                   # unknown users stop here
    route = router.classify(question)

    if route.path == "refused":
        access.audit(user=user, question=question, path="refused", category=route.category,
                     reason=route.reason)
        return contract.validate(contract.refused(question, user, route.reason, route.category)), []
    if route.path == "clarify":
        access.audit(user=user, question=question, path="clarify", category=route.category,
                     reason=route.reason)
        return contract.validate(contract.clarify(question, user, route.clarifying_question,
                                                  route.options, route.category)), []
    if route.unapplied and not allow_sql:         # nothing governed can answer it as asked
        reason = f"{route.reason.split(';')[0]}, and SQL is switched off."
        access.audit(user=user, question=question, path="refused", category=route.category,
                     reason=reason)
        return contract.validate(contract.refused(question, user, reason, route.category)), []
    if offline:
        return follow_route(route, question, user, allow_sql), []
    return ask_model(route, question, user, allow_sql, stats)


def follow_route(route, question, user, allow_sql=True):
    """Offline: run the router's request (or verified SQL) exactly."""
    if route.path == "sql_fallback":
        if not allow_sql:
            reason = "no governed metric matches this question, and SQL is switched off."
            return contract.validate(contract.refused(question, user, reason, "constrained"))
        if not route.sql:
            why = route.reason.split(";")[0] if route.unapplied else \
                "no governed metric matches this question"
            reason = (f"{why}, and offline mode does not write SQL. Ask a live model, or add "
                      "a verified query.")
            return contract.validate(contract.refused(question, user, reason, "constrained"))
        return tools.run_readonly_sql(user, route.sql, route.request.get("cutoff"),
                                      question, route.category)
    r = route.request
    return tools.query_metrics(user, r["metric"], r.get("group_by"), r.get("filters"),
                               r.get("start"), r.get("end"), r.get("cutoff"), question=question,
                               category=route.category, caveats=route.caveats,
                               reproduce=r.get("reproduce", False),
                               unfiltered=r.get("unfiltered", ()),
                               keep_version=r.get("keep_version", False))


def ask_model(route, question, user, allow_sql=True, stats=None):
    """Live: the model picks the tools; code binds the user and checks the result."""
    from serving import llm
    (access.REQUEST.get() or access.start_request())["model"] = llm.connect()[1]  # for the audit
    contracts, tools_used = [], []
    specs = [s for s in TOOL_SPECS if allow_sql or s["function"]["name"] != "run_readonly_sql"]

    def call_tool(name, arguments):
        if name == "list_metrics":
            return tools.list_metrics(user)
        if name == "get_dimensions":
            return tools.get_dimensions(user, arguments.get("metric", ""))
        if name == "query_metrics":
            blocked = tools.qualifier_blocked(route, arguments.get("metric", ""))
            if blocked:                             # it would answer a wider question
                access.audit(user=user, tool="query_metrics", question=question,
                             path="refused", reason=blocked, metric=arguments.get("metric"))
                return {"error": blocked}
            result = tools.query_metrics(
                user, arguments.get("metric", ""), arguments.get("group_by"),
                arguments.get("filters"), arguments.get("start"), arguments.get("end"),
                arguments.get("cutoff"), question=question, category=route.category,
                caveats=route.caveats, reproduce=route.request.get("reproduce", False),
                unfiltered=route.request.get("unfiltered", ()),
                keep_version=route.request.get("keep_version", False),
                bound_filters=route.request.get("bound_filters"))
        elif name == "run_readonly_sql" and allow_sql:
            governed = [c for c, tool in zip(contracts, tools_used) if tool == "query_metrics"]
            blocked = tools.fallback_blocked(route, governed, contracts)
            if blocked:                             # an attempted bypass: audited, not run
                access.audit(user=user, tool="run_readonly_sql", question=question,
                             path="refused", reason=blocked, sql=arguments.get("sql", ""))
                return {"error": blocked}
            result = tools.run_readonly_sql(user, arguments.get("sql", ""),
                                            arguments.get("cutoff"), question, "constrained")
        else:
            return {"error": f"There is no tool called {name}."}
        contracts.append(result)
        tools_used.append(name)
        return result

    text, calls = llm.chat_with_tools(system_prompt(route), question, specs, call_tool, stats=stats)
    return finish(question, user, text or "", contracts), calls     # None: out of turns


def system_prompt(route):
    from serving import clock
    hint = f"Today is {clock.long_date(clock.today())} (the demo clock). "
    # Which version is in force, generated from metric metadata on the clock's
    # date: no hand-written file states a date that can go stale.
    hint += "Versions today: " + " ".join(tools.version_status()) + " "
    hint += f"Router: category {route.category}, path {route.path}."
    if route.request.get("metric"):
        hint += f" Suggested request: { {k: v for k, v in route.request.items() if k not in ('reproduce', 'unfiltered', 'keep_version', 'bound_filters')} }."
    if route.verified_query:
        hint += f" Verified query {route.verified_query}: use that request exactly."
    if route.unapplied:
        hint += (f" The question asks only for {route.unapplied['meaning']}; no governed metric "
                 "applies that, so query_metrics will refuse: use run_readonly_sql, flagged.")
    if route.caveats:
        hint += " Caveats to pass on: " + " ".join(route.caveats)
    return (INSTRUCTIONS.read_text(encoding="utf-8") + "\n\n## This question\n\n" + hint +
            "\n\nAnswer in one to three sentences, number first. Do not repeat SQL, lineage or "
            "caveats word for word: the system attaches every caveat to your answer.")


def finish(question, user, text, contracts):
    """Pick the contract to return and decide whether the model's words may go with it."""
    answered = [c for c in contracts if c["path"] in ("governed", "sql_fallback")]
    if not answered:
        if contracts:
            return contracts[-1]                        # the layer's own refusal, with its reason
        return contract.validate(contract.refused(
            question, user, "the model gave no figure from the layer.", "constrained"))
    result = dict(answered[-1])
    if declines(text, result):
        # The words say "no figure" while the contract carries one (eval case
        # c15): a reader would see both. The refusal wins; no figure goes back.
        return contract.validate(contract.refused(
            question, user, "the assistant declined to give a figure for this question, so "
            "none is returned.", result["category"],
            caveats=[f"The assistant said: {text.strip()}"] if not stray_numbers(text, result) else []))
    stray = stray_numbers(text, result)
    unsigned = unsigned_negatives(text, result)
    if text and not stray and not unsigned:
        result["answer_text"] = text
    elif stray:
        result["caveats"] = result["caveats"] + [
            "The model's wording was withheld: it quoted figures the layer did not return ("
            + ", ".join(f"{f:,g}" for f in sorted(stray)) + ")."]
    elif unsigned:
        result["caveats"] = result["caveats"] + [
            "The model's wording was withheld: it gave a negative figure without saying it "
            "is negative (" + ", ".join(f"{f:,g}" for f in sorted(unsigned)) + ")."]
    return contract.validate(result)


# Words that decline to give a figure. With none of the contract's figures in
# the text, they mean the model is not standing behind the number.
DECLINE = re.compile(r"\b(can(?:no|['’])?t|unable to|not able to|won['’]t|do(?:es)?n['’]t have|"
                     r"no governed (?:metric|figure|definition)|not possible)\b", re.I)


def declines(text, result):
    """True if the wording declines to give a figure and quotes none of the contract's."""
    figures = {round(abs(float(v)), 4) for row in result["values"] for v in row.values()
               if isinstance(v, (int, float)) and not isinstance(v, bool)}
    if result.get("total") is not None:
        figures.add(round(abs(float(result["total"])), 4))
    quoted = any(abs(n - f) <= 0.005 for n in numbers_in(text) for f in figures)
    return bool(text and DECLINE.search(text) and not quoted)


# A number in text: optional sign and £, digits with commas, decimals, then an
# optional unit. "£50", "£50.00", "50 GBP", "50 pounds", "−£70" and "2.29%" all parse.
NUMBER = re.compile(r"[£$€]?\s?(\d[\d,]*(?:\.\d+)?)\s?(%|percent|per cent)?", re.I)


def numbers_in(text):
    """Every number in a piece of text, as a value (sign ignored, % as a fraction too)."""
    found = set()
    for digits, percent in NUMBER.findall(text or ""):
        value = round(float(digits.replace(",", "")), 4)
        found.add(value)
        if percent:
            found.add(round(value / 100, 6))
    return found


def stray_numbers(text, result):
    """Numbers in the model's wording that the contract cannot account for.

    Compared as values, not strings, so "£50", "£50.00" and "50 GBP" are the
    same figure. Every number counts, not only money: a bare "60" is checked
    too. Numbers the contract carries anywhere outside its SQL (values, total,
    dates, cutoff, version) and numbers in the question itself are allowed.
    Anything else, including a sum or difference the model worked out, is
    stray, and the wording is withheld: this fails safe.
    """
    allowed = {round(abs(float(v)), 4) for row in result["values"] for v in row.values()
               if isinstance(v, (int, float)) and not isinstance(v, bool)}
    if result.get("total") is not None:
        allowed.add(round(abs(float(result["total"])), 4))
    for v in list(allowed):
        allowed.add(round(v * 100, 4))                      # a fraction quoted as a percentage
    context = {k: v for k, v in result.items() if k not in ("compiled_sql", "answer_text", "generated_at")}
    # ensure_ascii=False: by default json.dumps writes "£50" as "\u00a350", which
    # reads as the number 350, so a figure the user quoted in the question was
    # treated as stray and the wording withheld (held-out case h2_11).
    allowed |= {abs(n) for n in numbers_in(json.dumps(context, default=str, ensure_ascii=False))}
    allowed |= numbers_in(result.get("question") or "")
    stray = set()
    for n in numbers_in(text):
        if not any(abs(n - a) <= 0.005 for a in allowed):
            stray.add(n)
    return stray


# Words that say a figure is below zero. A minus sign must sit right before
# the number ("−£70", "-70"); a word may be anywhere near it in the sentence.
NEGATIVE_WORDS = re.compile(r"\b(minus|loss|losses|lost|lose|down|negative|below zero|deficit)\b", re.I)


def unsigned_negatives(text, result):
    """Negative figures from the contract that the wording gives as positive.

    "Lighting made £70" when lighting is −£70 quotes the right digits and the
    wrong meaning. For every figure that matches a negative value (and no
    positive one) the sentence must mark it: a minus sign before it, or a word
    such as loss, lost, down or negative within 30 characters of it.
    """
    values = [float(v) for row in result["values"] for v in row.values()
              if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if result.get("total") is not None:
        values.append(float(result["total"]))
    negative = {round(-v, 4) for v in values if v < 0}
    positive = {round(v, 4) for v in values if v >= 0}
    unsigned = set()
    for match in NUMBER.finditer(text or ""):
        value = round(float(match.group(1).replace(",", "")), 4)
        if value not in negative or value in positive:
            continue
        before = re.split(r"[.!?;:]\s", text[:match.start()])[-1]
        after = re.split(r"[.!?;:]\s", text[match.end():])[0]
        signed = re.search(r"[−-]\s?$", before)
        worded = NEGATIVE_WORDS.search(before[-30:]) or NEGATIVE_WORDS.search(after[:30])
        if not (signed or worded):
            unsigned.add(value)
    return unsigned
