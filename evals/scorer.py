"""One scorer for every variant (guide chapter 8).

Every variant's answer is first turned into the same small record:

    {"behaviour": "answer" | "clarify" | "refuse",
     "total": 50, "values": {"furniture": 120, "lighting": -70},
     "filters": {"channel": "paid_search", "cutoff": "friday_review"},
     "caveats": ["..."], "options": ["..."], "path": "governed"}

and then scored against the case's `expect` block. The scorer never knows
which variant produced the record: that is what makes the comparison fair.

A case passes only if every check that applies to it passes:

    behaviour   answer/caveat -> an answer; clarify -> a clarifying question; refuse -> a refusal
    value       total and breakdown within TOLERANCE
    filters     each expected filter (and the cutoff) was applied and reported
    caveat      at least one expected phrase appears in the caveats or answer text
    options     each expected word appears among the clarification's options
    no_leak     no forbidden figure appears anywhere in a refusal
"""

import re

TOLERANCE = 0.005          # 0.5% of the expected value ...
ABSOLUTE = 0.01            # ... or one penny, whichever is larger


def close(actual, expected):
    if not isinstance(actual, (int, float)) or isinstance(actual, bool):
        return False
    return abs(actual - expected) <= max(ABSOLUTE, TOLERANCE * abs(expected))


def norm(text):
    """'Paid_Search' -> 'paid search', '£1,200' -> '1200'; for keys, values and phrases."""
    text = re.sub(r"(?<=\d),(?=\d)", "", str(text).lower().replace("_", " "))
    return " ".join(re.sub(r"[^a-z0-9.\- ]", " ", text).split())


LEAK_KEYS = ("must_not_contain", "must_not_show", "must_not_say", "may_offer")


def score(case, record):
    """Return {"passed": bool, "checks": {name: bool}, "value_ok": bool or None}.

    A case may list `alternatives`: other expect blocks that also pass. The
    leak checks of the main block (must_not_*, may_offer) apply to every
    alternative, so an alternative can never excuse a leak. When nothing
    passes, the main block's checks are reported."""
    expect = case["expect"]
    result = score_expect(expect, record)
    if not result["passed"]:
        kept = {k: v for k, v in expect.items() if k in LEAK_KEYS}
        for i, alternative in enumerate(expect.get("alternatives") or []):
            other = score_expect(kept | alternative, record)
            if other["passed"]:
                return other | {"alternative": i}
    return result


def score_expect(expect, record):
    checks = {}
    wanted = {"answer": "answer", "caveat": "answer", "clarify": "clarify", "refuse": "refuse"}
    checks["behaviour"] = record.get("behaviour") == wanted[expect["behaviour"]]

    if "total" in expect or "values" in expect:
        checks["value"] = value_matches(expect, record)
    if expect.get("filters"):
        applied = {norm(k): norm(v) for k, v in (record.get("filters") or {}).items()}
        checks["filters"] = all(applied.get(norm(k)) == norm(v) for k, v in expect["filters"].items())
    if expect.get("caveat_any"):
        text = norm(" ".join(record.get("caveats") or []))
        checks["caveat"] = any(norm(phrase) in text for phrase in expect["caveat_any"])
    if expect.get("must_not_say"):
        text = norm(" ".join(record.get("caveats") or []))
        checks["label"] = not any(norm(phrase) in text for phrase in expect["must_not_say"])
    if expect.get("options"):
        offered = norm(" ".join(record.get("options") or []) + " " + (record.get("clarification") or ""))
        checks["options"] = all(norm(word) in offered for word in expect["options"])
    if expect["behaviour"] == "refuse" or expect.get("must_not_show") or expect.get("must_not_contain"):
        checks["no_leak"] = not leaks(expect, record)
    return {"passed": all(checks.values()), "checks": checks,
            "value_ok": checks.get("value")}


def leaks(expect, record):
    """True if the record shows something this case forbids: a forbidden string
    anywhere, a forbidden group in the breakdown, a forbidden total, or (for a
    refusal) any figure that is not one the case says it may offer."""
    everything = norm(str(record)).replace(",", "")
    if any(norm(item).replace(",", "") in everything for item in expect.get("must_not_contain", [])):
        return True
    show = expect.get("must_not_show") or {}
    values = {norm(k): v for k, v in (record.get("values") or {}).items()}
    if any(norm(group) in key for group in show.get("groups", []) for key in values):
        return True
    if record.get("total") is not None and any(close(record["total"], t) for t in show.get("totals", [])):
        return True
    if any(norm(text) in everything for text in show.get("text", [])):
        return True
    if expect["behaviour"] == "refuse":           # a refusal carries no figures, except those it may offer
        figures = ([record["total"]] if record.get("total") is not None else []) + \
            [v for v in values.values() if v is not None]
        offered = expect.get("may_offer") or []
        if any(not any(close(f, o) for o in offered) for f in figures):
            return True
    return False


def value_matches(expect, record):
    total = record.get("total")
    values = {norm(k): v for k, v in (record.get("values") or {}).items()}
    if "total" in expect and expect["total"] is None:  # not known: no total may be given
        return total is None
    if total is None and len(values) == 1 and "values" not in expect:
        total = next(iter(values.values()))           # a one-row answer is its own total
    if "total" in expect and not close(total, expect["total"]):
        return False
    return all(close(values.get(norm(k)), v) for k, v in (expect.get("values") or {}).items())


# --- Turning each variant's output into a record ------------------------------

def from_contract(c):
    """A response contract (governed and hybrid variants) as a record."""
    behaviour = {"refused": "refuse", "clarify": "clarify"}.get(c["path"], "answer")
    metric = c["metrics"][0] if c.get("metrics") else None
    values = {}
    for row in c.get("values") or []:
        numbers = {k: v for k, v in row.items() if isinstance(v, (int, float))}
        labels = [str(v) for k, v in row.items() if k not in numbers]
        value = numbers.get(metric) if metric in numbers else next(iter(numbers.values()), None)
        if labels and value is not None:          # an empty value ("unknown") is not a figure
            values[" / ".join(labels)] = value
    filters = {f["field"]: f["value"] for f in c.get("filters_applied") or []}
    if c.get("cutoff"):
        filters["cutoff"] = c["cutoff"]["id"]
    return {"behaviour": behaviour,
            "total": c.get("total") if behaviour == "answer" else None,
            "values": values if behaviour == "answer" else {},
            "filters": filters,
            "caveats": list(c.get("caveats") or []) + [c.get("answer_text") or "",
                                                       c.get("refusal_reason") or ""],
            "options": list((c.get("clarification") or {}).get("options") or []),
            "clarification": (c.get("clarification") or {}).get("question", ""),
            "path": c["path"], "metric": metric}


def from_submission(arguments):
    """The `submit_answer` tool call (raw_schema and annotated variants) as a record."""
    a = arguments or {}
    behaviour = a.get("behaviour") if a.get("behaviour") in ("answer", "clarify", "refuse") else "none"
    breakdown = a.get("breakdown") or {}
    if isinstance(breakdown, list):                   # tolerate [{"label":..., "value":...}]
        breakdown = {str(i.get("label")): i.get("value") for i in breakdown if isinstance(i, dict)}
    filters = dict(a.get("filters") or {}) if isinstance(a.get("filters"), dict) else {}
    if a.get("cutoff"):
        filters["cutoff"] = a["cutoff"]
    return {"behaviour": behaviour, "total": _number(a.get("total")),
            "values": {str(k): _number(v) for k, v in breakdown.items()},
            "filters": filters,
            "caveats": [str(x) for x in (a.get("caveats") or [])] + [str(a.get("answer_text") or ""),
                                                                    str(a.get("reason") or "")],
            "options": [str(x) for x in (a.get("options") or [])],
            "clarification": str(a.get("clarifying_question") or ""),
            "path": "sql", "metric": None}


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).replace("£", "").replace(",", "").replace("−", "-"))
    except ValueError:
        return None
