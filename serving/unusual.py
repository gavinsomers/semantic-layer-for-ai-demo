"""Unusual values (guide ch 10): flag a figure far outside its metric's history.

A wrong load or a broken join often shows first as a number nobody has seen
before. `sl ranges` stores, per metric, the usual range of its DAILY values
at the latest cutoff, with the metric's default filters: the mean plus or
minus Z standard deviations (a z-score test; a day with no orders counts as
0 for a sum or count). An answer is then compared with it:

    additive metrics (sums, counts, contribution)   total / days in the window
    ratios and stock positions                       the figure itself

and, if it falls outside, the answer carries a caveat. It is a prompt to
check, never a refusal: real business can be unusual. The check runs only
where the history fits the request (same filters, a known window), and only
with at least MIN_DAYS days of history.
"""

import json
import statistics
from datetime import date
from pathlib import Path

from serving import layer

RANGES = Path(__file__).resolve().parent / "expected_ranges.json"
MIN_DAYS = 8
Z = 3                                     # how many standard deviations count as unusual
HISTORY_CUTOFF = "september_close"


def kind(metric):
    """'per_day' if a window's total grows with its days, 'level' if not, None to skip."""
    definition = layer.metrics()[metric]
    if definition["type"] in ("cumulative", "conversion"):
        return None
    if definition["type"] == "ratio":
        return "level"
    leaves = layer._leaves(metric, layer.manifest())
    if any(leaf.get("non_additive_dimension") for leaf in leaves):
        return "level"                                   # a stock position: never summed over days
    return "per_day"


def compute(metric):
    """The usual daily range of one metric, or None if there is too little history."""
    shape = kind(metric)
    if shape is None:
        return None
    filters = layer.metrics()[metric]["meta"].get("defaults", {}).get("filters", {})
    where = [layer.where_clause("cutoff", HISTORY_CUTOFF)]
    where += [layer.where_clause(k, v) for k, v in filters.items()]
    rows = layer.query([metric], ["metric_time__day"], where, with_total=False)["rows"]
    daily = {date.fromisoformat(str(row["metric_time__day"])[:10]): row[metric] for row in rows
             if row.get(metric) is not None}
    if shape == "per_day" and daily:                     # a day with no orders sold nothing
        first, last = min(daily), max(daily)
        daily = {date.fromordinal(d): daily.get(date.fromordinal(d), 0)
                 for d in range(first.toordinal(), last.toordinal() + 1)}
    values = list(daily.values())
    if len(values) < MIN_DAYS:
        return None
    mean, spread = statistics.mean(values), statistics.stdev(values)
    low = mean - Z * spread
    if min(values) >= 0:
        low = max(low, 0)                        # a metric that is never negative
    return {"kind": shape, "filters": filters, "days": len(values), "cutoff": HISTORY_CUTOFF,
            "low": round(low, 4), "high": round(mean + Z * spread, 4)}


def write(path=RANGES):
    """`sl ranges`: recompute and store every visible metric's range."""
    ranges = {}
    for name, definition in layer.metrics().items():
        if not definition["meta"].get("hidden"):
            found = compute(name)
            if found:
                ranges[name] = found
    Path(path).write_text(json.dumps(ranges, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return ranges


def load(path=RANGES):
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {}


def check(metric, filters, total, start=None, end=None):
    """None if the figure is usual (or cannot be judged), else what was found:
    {"value", "per_day", "low", "high", "days", "cutoff"}."""
    usual = load().get(metric)
    if not usual or total is None or dict(filters) != usual["filters"]:
        return None
    value = total
    if usual["kind"] == "per_day":
        if not (start and end):
            return None
        value = total / ((date.fromisoformat(end) - date.fromisoformat(start)).days + 1)
    if usual["low"] <= value <= usual["high"]:
        return None
    return {"value": round(value, 4), "per_day": usual["kind"] == "per_day", **usual}
