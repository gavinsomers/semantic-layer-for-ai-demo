"""Classify a question before anything runs (guide chapter 4; book chapter 4).

Deterministic rules read from context/: no model is needed, so the same
question is always routed the same way and the routing can be tested.

    unsupported  -> refuse, with the reason; nothing is queried
    constrained  -> one named-choice question, or an answer with its condition
    supported    -> a governed request

The result is a Route: the category, the path the answer should take, and a
draft request (metric, breakdowns, filters, dates, cutoff) for the layer.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from serving import clock, layer

CONTEXT = Path(__file__).resolve().parent.parent / "context"


@dataclass
class Route:
    category: str                      # supported | constrained | unsupported
    path: str                          # governed | sql_fallback | refused | clarify
    reason: str = ""
    request: dict = field(default_factory=dict)
    verified_query: str | None = None
    sql: str | None = None
    clarifying_question: str | None = None
    options: list = field(default_factory=list)
    caveats: list = field(default_factory=list)
    unapplied: dict | None = None      # a qualifier the request cannot apply (synonyms.yml)


def load_context():
    return {name: yaml.safe_load((CONTEXT / f"{name}.yml").read_text(encoding="utf-8"))
            for name in ("synonyms", "ambiguity", "verified_queries")}


def classify(question, context=None):
    context = context or load_context()
    q = normalise(question)              # lower case, no punctuation, plurals folded
    rules = context["ambiguity"]

    # 1. Unsupported: refuse before any query. These are regular expressions
    #    written against the words as typed, so they read the unfolded text.
    for rule in rules["unsupported"]:
        if any(re.search(p, plain(question)) for p in rule["patterns"]):
            reason = rule["reason"] + (f" I can give instead: {rule['offer']}" if rule.get("offer") else "")
            return Route("unsupported", "refused", reason=reason)

    # 2. A verified query: the request someone has already checked.
    for vq in context["verified_queries"]["verified_queries"]:
        if any(similar(q, normalise(phrasing)) for phrasing in vq["questions"]):
            if vq["kind"] == "sql":
                return Route(vq["category"], "sql_fallback", reason=f"verified SQL {vq['id']}",
                             verified_query=vq["id"], sql=vq["sql"],
                             request={"cutoff": vq.get("cutoff")})
            route = Route(vq["category"], "governed", reason=f"verified query {vq['id']}",
                          verified_query=vq["id"], request=dict(vq["request"]))
            # A near match can still ask for more ("..., by product group", "...
            # for furniture"): keep the extra breakdown and filters, so the layer
            # answers them or the constraints and access rules refuse them openly.
            asked = parse(q, context["synonyms"])
            extra = [g for g in asked.get("group_by", [])
                     if g not in route.request.get("group_by", [])]
            if extra:
                route.request["group_by"] = list(route.request.get("group_by", [])) + extra
            if asked.get("filters"):
                route.request["filters"] = {**route.request.get("filters", {}), **asked["filters"]}
            if names_past_report(q, rules):          # a hold does not stop a past report
                route.request["reproduce"] = True
            return _finish(route, q, rules)

    # 3. Ambiguous words: ask one named-choice question, or answer the default.
    caveats, default_metric = [], None
    for rule in rules["ambiguities"]:
        if not any(contains(q, term) for term in rule["terms"]):
            continue
        settled = any(contains(q, word) for word in rule.get("unless_any", []))
        if settled:
            default_metric = rule.get("default_in_context")
            continue
        if rule["policy"] == "ask":
            return Route("constrained", "clarify", reason=f"ambiguous: {rule['id']}",
                         clarifying_question=" ".join(rule["clarifying_question"].split()),
                         options=rule["options"])
        default_metric = rule["default"]
        caveats.append(" ".join(rule["caveat"].split()))

    # 4. Synonyms: words to a metric, breakdowns, filters and dates.
    request = parse(q, context["synonyms"])
    if default_metric and "metric" not in request:
        request["metric"] = default_metric
    if "metric" not in request:
        follow_up = bare_follow_up(question, request, context["synonyms"])
        if follow_up:                      # "And for furniture?": a filter, but no figure
            return follow_up
        return Route("constrained", "sql_fallback", request=request,
                     reason="no governed metric matches; read-only SQL fallback, flagged")
    unapplied = unapplied_qualifier(q, request["metric"], context["synonyms"])
    if unapplied:                          # a governed answer would answer a wider question
        return Route("constrained", "sql_fallback", request=request, unapplied=unapplied,
                     reason=f"the question asks only for {unapplied['meaning']}, and no "
                            f"governed metric or filter applies that to {request['metric']}; "
                            "read-only SQL fallback, flagged")
    if names_past_report(q, rules):                  # a hold does not stop a past report
        request["reproduce"] = True
    route = Route("constrained" if caveats else "supported", "governed",
                  reason=f"synonym match: {request['metric']}", request=request, caveats=caveats)
    return _finish(route, q, rules)


def _finish(route, q, rules):
    """Versions, constraints, then whether the window can be answered at all."""
    route = _with_constraints(_with_versions(route, q, rules), rules)
    if names_old_version(q, rules):        # a past report or the old rule, by name
        route.request["keep_version"] = True
    bound = bound_filters(q, route.request.get("filters") or {})
    if bound:
        route.request["bound_filters"] = bound
    return _with_coverage(route)


def bound_filters(q, filters, synonyms=None):
    """The filters the question pins down: a dimension for which it names
    exactly one value ("how many sofas"). The layer applies them even when a
    model's request leaves them out (serving/tools.py), so a question about one
    product never gets the all-product figure. A question that names two values
    of a dimension ("furniture and lighting") binds neither: it may be a
    comparison."""
    synonyms = synonyms or load_context()["synonyms"]
    bound = {}
    for dimension, value in filters.items():
        named = {name for name, words in (synonyms["values"].get(dimension) or {}).items()
                 if any(contains(q, w) for w in words)}
        if named == {value}:
            bound[dimension] = value
    return bound


def _with_coverage(route):
    """A window that cuts through an advertising period cannot be answered by a
    metric that deducts advertising: ask which whole-period window is meant
    (serving/tools.py, advertising_gap), before any model or query runs."""
    from serving import tools
    r = route.request
    meta = layer.metrics().get(r.get("metric"), {}).get("meta", {})
    defaults = meta.get("defaults", {})
    filters = {**{k: v for k, v in defaults.get("filters", {}).items()
                  if k not in r.get("unfiltered", ())}, **r.get("filters", {})}
    gap = tools.advertising_gap(r.get("metric"), filters, r.get("start") or defaults.get("start"),
                                r.get("end") or defaults.get("end"),
                                r.get("cutoff") or defaults.get("cutoff") or tools.DEFAULT_CUTOFF)
    if not gap:
        return route
    return Route("constrained", "clarify", reason="the window cuts through an advertising period",
                 request=r, clarifying_question=gap["question"], options=gap["options"])


def bare_follow_up(question, request, synonyms):
    """A follow-up that names a filter or breakdown but no figure ("And for
    furniture?"), as a named-choice question; None for any other question.

    Each question is routed on its own, with no memory of the one before, so
    there is no metric to carry over. Sending it to the SQL fallback would have
    a model guess the figure; ask which one instead, naming the metrics that
    can take the filter."""
    if not (request.get("filters") or request.get("group_by")):
        return None
    rest = normalise(question)
    phrases = [w for values in synonyms["values"].values() for words in values.values() for w in words]
    phrases += [w for words in synonyms["dimensions"].values() for w in words]
    phrases += [p for period in synonyms["time_phrases"] for p in period["phrases"]]
    for phrase in sorted(phrases, key=len, reverse=True):
        rest = re.sub(rf"\b{re.escape(normalise(phrase))}\b", " ", rest)
    if set(rest.split()) - FOLLOW_UP_WORDS:
        return None                        # it says something else: not a bare follow-up
    from serving import tools
    keys = list(request.get("filters", {})) + list(request.get("group_by", []))
    options = [name for name, m in layer.metrics().items()
               if not m["meta"].get("hidden") and in_force_or_unversioned(name)
               and not any(k in m["meta"].get("cannot_group_by", []) for k in keys)
               and all(layer.resolve_dimension(name, k) for k in keys)]
    labels = [layer.metrics()[name]["label"] for name in options]
    scope = ", ".join(str(v).replace("_", " ") for v in request.get("filters", {}).values()) \
        or "that breakdown"
    return Route("constrained", "clarify", reason="a follow-up with a filter but no metric",
                 request=request, options=options,
                 clarifying_question=(f"Which figure do you want for {scope}? Each question is "
                                      "answered on its own, so name it: "
                                      + "; ".join(labels) + "."))


# Words a bare follow-up may hold besides its filter ("And what about furniture, then?").
FOLLOW_UP_WORDS = set("and what about how for the then same now please also just only "
                      "so is was were are a an too as well in on of by at it that those "
                      "these what's whats show me give tell".split())


def in_force_or_unversioned(metric):
    """Leave out a version that is not in force yet, and one a successor replaced."""
    rules = load_context()["ambiguity"].get("versions") or []
    if any(r["successor"] == metric for r in rules) and not in_force(metric):
        return False
    return not any(r["metric"] == metric and in_force(r["successor"]) for r in rules)


def names_old_version(q, rules):
    """Does the question name a past report or an old rule by name? Then a
    superseded version answers as asked (serving/tools.py keeps it)."""
    words = [w for rule in rules.get("versions") or [] for w in rule["reproduce_when_any"]]
    return names_past_report(q, rules) or any(contains(q, w) for w in words)


def parse(q, synonyms):
    """Turn the words of a question into a draft request for the layer."""
    request = {}
    counted = q
    for phrase in synonyms.get("not_metrics") or []:      # "order lines" is not "orders"
        counted = re.sub(rf"\b{re.escape(normalise(phrase))}\b", " ", counted)
    metric = longest_match(counted, synonyms["metrics"])
    # Word sets: every word present, in any order ("the refunds we'd approved"
    # asks for the approved-refund rule as plainly as "approved refunds"), and
    # competing with phrases by length.
    best = max((len(w) for w in synonyms["metrics"].get(metric, []) if contains(counted, w)),
               default=0)
    for name, sets in (synonyms.get("metric_word_sets") or {}).items():
        for words in sets:
            if all(contains(counted, w) for w in words) and len(" ".join(words)) > best:
                metric, best = name, len(" ".join(words))
    if metric:
        request["metric"] = metric
    group_by, rest = [], q
    for dimension, words in (synonyms.get("wider") or {}).items():
        for word in sorted(words, key=len, reverse=True):
            if contains(rest, word):               # "across all channels": no default channel,
                rest = re.sub(rf"\b{re.escape(normalise(word))}\b", " ", rest)   # no breakdown
                request.setdefault("unfiltered", []).append(dimension)
                break
    phrases = sorted(((len(w), w, name) for name, words in synonyms["dimensions"].items()
                      for w in words), reverse=True)
    for _, word, name in phrases:          # longest first: "by product group" before "by product"
        if contains(rest, word):
            rest = re.sub(rf"\b{re.escape(normalise(word))}\b", " ", rest)
            if name not in group_by:
                group_by.append(name)
    if group_by:
        request["group_by"] = group_by
    filters = {}
    for dimension, values in synonyms["values"].items():
        value = longest_match(q, values)
        if value:
            filters[dimension] = value
    if filters:
        request["filters"] = filters
    for period in synonyms["time_phrases"]:
        if any(contains(q, p) for p in period["phrases"]):
            request.update({k: period[k] for k in ("start", "end", "cutoff") if k in period})
            request["at_cutoff"] = period.get("at_cutoff", False)
            break
    cutoff = longest_match(q, synonyms.get("cutoff_phrases", {}))
    if cutoff:                             # a named cutoff beats a period's default
        request["cutoff"] = cutoff
    if request.pop("at_cutoff", False) and request.get("metric") and \
            layer.agg_time_dimensions(request["metric"]) == {"snapshot_date"}:
        # A position "at the Friday review" is the one recorded by its cutoff:
        # the cutoff's own (UK) date, not the last day of the review's order window.
        from serving import tools
        day = tools.cutoffs()[request["cutoff"]][:10]
        request["start"] = request["end"] = day
    return request


def unapplied_qualifier(q, metric, synonyms):
    """The first qualifier in the question that this metric does not carry, or None."""
    for rule in synonyms.get("qualifiers") or []:
        if metric not in rule["carried_by"] and any(contains(q, w) for w in rule["words"]):
            return rule
    return None


def names_past_report(q, rules):
    """Does the question ask to reproduce a report already made (the Friday review)?"""
    return any(contains(q, phrase) for phrase in rules.get("past_reports") or [])


def in_force(metric):
    """Is this metric's meaning in force on the demo clock's date? A metric with
    an effective_from in its meta is not, until that date."""
    start = layer.metrics().get(metric, {}).get("meta", {}).get("effective_from")
    return start is None or clock.today() >= date.fromisoformat(str(start))


def _with_versions(route, q, rules):
    """Once a successor is in force, it answers current questions; a question that
    names a report made under the old version reproduces it with the old one."""
    metric = route.request.get("metric")
    for rule in rules.get("versions") or []:
        if metric != rule["metric"] or not in_force(rule["successor"]):
            continue
        if not any(contains(q, w) for w in rule["reproduce_when_any"] + rules["past_reports"]):
            route.request["metric"] = rule["successor"]       # tools.py adds the version note
            route.reason += f"; {rule['successor']} answers current questions now"
    return route


def _with_constraints(route, rules):
    """Mark a request constrained when the definition only partly supports it."""
    metric = route.request.get("metric")
    meta = layer.metrics().get(metric, {}).get("meta", {})
    for rule in rules["constraints"]:
        if metric not in rule["applies_to"]:
            continue
        if rule["id"] == "comparison_only" and not in_force(metric):
            route.category = "constrained"      # the caveat comes with the answer, once
        if rule["id"] == "no_split_below_group":
            asked = route.request.get("group_by", [])
            blocked = [g for g in asked if g in meta.get("cannot_group_by", [])]
            if blocked:
                reasons = rule["reason_by_metric"]
                route.category = "constrained"
                route.request["group_by"] = [g for g in asked if g not in blocked]
                route.caveats.append(f"Not split by {', '.join(blocked)}: "
                                     + reasons.get(metric, reasons["default"])
                                     + " The whole figure is given instead.")
    return route


# --- Text matching ----------------------------------------------------------

def plain(text):
    """Lower case, hyphens to spaces, punctuation out: 'Paid-search margins?' -> 'paid search margins'."""
    text = text.lower().replace("’", "'").replace("-", " ")
    return " ".join(re.sub(r"[^a-z0-9' ]", " ", text).split())


def normalise(text):
    """plain(), then each word folded to a simple stem, so a rule written for
    'margin' also catches 'margins' and "the trial's" matches 'the trial'.
    Applied to questions AND to every phrase in context/, so both sides fold
    the same way: 'margins' -> 'margin', 'categories' -> 'category',
    'boxes' -> 'box', "friday's" -> 'friday'. No dictionary, on purpose: a
    few suffix rules are predictable and testable."""
    return " ".join(stem(word) for word in plain(text).split())


def stem(word):
    word = word.removesuffix("'s").rstrip("'")
    if len(word) <= 3 or word.endswith(("ss", "us", "is")):
        return word                                   # gas, address, status, analysis
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"                        # categories -> category
    if word.endswith("es") and word[:-2].endswith(("s", "x", "z", "ch", "sh")):
        return word[:-2]                              # boxes -> box, addresses -> address
    if word.endswith("s"):
        return word[:-1]                              # margins -> margin, orders -> order
    return word


def contains(q, phrase):
    return re.search(rf"\b{re.escape(normalise(phrase))}\b", q) is not None


def longest_match(q, names_to_words):
    """The name whose longest matching phrase is longest ("approved refunds" > "refunds")."""
    best, best_length = None, 0
    for name, words in names_to_words.items():
        for word in words:
            if contains(q, word) and len(word) > best_length:
                best, best_length = name, len(word)
    return best


def similar(a, b, threshold=0.8):
    """Same question, give or take a word or two (token overlap)."""
    x, y = set(a.split()), set(b.split())
    return a == b or (len(x & y) / len(x | y) >= threshold if x | y else False)
