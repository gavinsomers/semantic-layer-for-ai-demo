"""Metric regression on frozen data (guide chapter 6).

    uv run python -m sl regress

Each entry in ci/expected_metrics.yml is a request and the answer it must
give. They are asked through the same tool the assistant uses
(serving/tools.py), so the check covers the metric definition, the joins
MetricFlow chooses and the defaults applied, not just the SQL models.
"""

from contextlib import contextmanager
from pathlib import Path

import yaml

from serving import layer, tools

EXPECTED = Path(__file__).resolve().parent / "expected_metrics.yml"
TOLERANCE = 0.0001


@contextmanager
def project(dbt_dir):
    """Point the layer at another dbt project (a scratch copy) for a moment."""
    saved = layer.DBT_DIR, layer.MANIFEST, layer.WAREHOUSE
    dbt_dir = Path(dbt_dir)
    layer.DBT_DIR, layer.MANIFEST = dbt_dir, dbt_dir / "target" / "semantic_manifest.json"
    layer.WAREHOUSE = dbt_dir.parent / "warehouse.duckdb"      # profiles.yml: ../warehouse.duckdb
    try:
        yield
    finally:
        layer.DBT_DIR, layer.MANIFEST, layer.WAREHOUSE = saved


def ask(entry):
    return tools.query_metrics("finance_lead", entry["metric"], entry.get("group_by"),
                               entry.get("filters"), entry.get("start"), entry.get("end"),
                               entry.get("cutoff"))


def ask_in(dbt_dir, metric, group_by=()):
    """One governed answer from a scratch project (used by the change demo)."""
    with project(dbt_dir):
        return tools.query_metrics("finance_lead", metric, list(group_by))


def compare(entry, result):
    """None if the answer matches, else what differs."""
    if result["path"] != "governed":
        return f"refused: {result['refusal_reason']}"
    problems = []
    if abs((result["total"] or 0) - entry["total"]) > TOLERANCE or result["total"] is None:
        problems.append(f"total expected {entry['total']}, got {result['total']}")
    metric = entry["metric"]
    for group, value in (entry.get("values") or {}).items():
        got = next((row[metric] for row in result["values"] if group in row.values()), None)
        if got is None or abs(got - value) > TOLERANCE:
            problems.append(f"{group} expected {value}, got {got}")
    return "; ".join(problems) or None


def check(dbt_dir=None, only=None):
    """Lines of PASS/FAIL, one per expected answer."""
    entries = yaml.safe_load(EXPECTED.read_text(encoding="utf-8"))["expected"]
    lines = []
    with project(dbt_dir or layer.DBT_DIR):
        for entry in entries:
            if only and entry["metric"] not in only:
                continue
            problem = compare(entry, ask(entry))
            lines.append(f"{'FAIL' if problem else 'PASS'} {entry['id']}"
                         + (f": {problem}" if problem else ""))
    return lines


def main(_args=None):
    lines = check()
    print("\n".join(lines))
    failed = [line for line in lines if line.startswith("FAIL")]
    print(f"\n{len(lines) - len(failed)} of {len(lines)} expected metric values match.")
    return 1 if failed else 0
