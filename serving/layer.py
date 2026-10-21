"""A thin wrapper around the MetricFlow CLI (`mf`), the one door to the numbers.

Guide chapter 4. Everything here shells out to `mf` exactly as you would type
it, with the dbt project as the working directory, so a reader can copy any
command from a response and run it by hand:

    mf list metrics
    mf list dimensions --metrics goods_value
    mf query --metrics goods_value --group-by channel --explain
    mf query --metrics goods_value --group-by channel --csv out.csv

Metric metadata (owner, version, status...) is read from the semantic
manifest that `dbt build` writes, because `mf list metrics` does not print it.
"""

import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DBT_DIR = ROOT / "dbt"
WAREHOUSE = ROOT / "warehouse.duckdb"
MANIFEST = DBT_DIR / "target" / "semantic_manifest.json"
BIN = Path(sys.executable).parent      # .venv/bin, or .venv\Scripts on Windows

# Names that are MetricFlow entities rather than dimensions. Filters on them
# are written {{ Entity('x') }}; anything else is {{ Dimension('x') }}.
ENTITIES = {"channel", "product_group", "cutoff", "order", "customer"}


class LayerError(Exception):
    """MetricFlow refused or failed. The message is safe to show."""


def tool(name):
    """Path to a console script in this environment (mf, dbt), on any OS."""
    found = shutil.which(name, path=str(BIN))
    if not found:
        raise LayerError(f"'{name}' is not installed here. Run `uv sync` first.")
    return found


def run_mf(*arguments):
    """Run `mf <arguments>` in the dbt directory and return its output."""
    # PYTHONUTF8: mf prints emoji; on Windows a pipe would otherwise be cp1252.
    result = subprocess.run([tool("mf"), *arguments], cwd=DBT_DIR, capture_output=True,
                            text=True, encoding="utf-8", env={**os.environ, "PYTHONUTF8": "1"})
    output = result.stdout + result.stderr
    if result.returncode != 0 or "ERROR" in output or "found a bug" in output:
        raise LayerError(_last_error(output))
    return output


# --- Listing ----------------------------------------------------------------

def manifest():
    """The semantic manifest written by `dbt build` / `dbt parse`."""
    if not MANIFEST.exists():
        raise LayerError("No semantic manifest. Run `uv run python -m sl build` first.")
    stamp = (str(MANIFEST), MANIFEST.stat().st_mtime_ns)
    if _MANIFEST_CACHE.get("stamp") != stamp:           # re-read only after a rebuild
        _MANIFEST_CACHE.update(stamp=stamp, data=json.loads(MANIFEST.read_text(encoding="utf-8")))
    return json.loads(json.dumps(_MANIFEST_CACHE["data"]))


_MANIFEST_CACHE = {}


def metrics():
    """Every metric with its label, description, type and operating agreement,
    sorted by name: dbt does not keep the manifest's metric order between parses."""
    return {m["name"]: {"name": m["name"], "label": m.get("label") or m["name"],
                        "description": " ".join((m.get("description") or "").split()),
                        "type": m["type"],
                        "meta": (m.get("config") or {}).get("meta", {})}
            for m in sorted(manifest()["metrics"], key=lambda m: m["name"])}


def list_metrics_cli():
    """`mf list metrics`: names and the dimensions each one can be grouped by."""
    return _bullets(run_mf("list", "metrics"))


def list_dimensions(metric):
    """`mf list dimensions` and `mf list entities` for one metric."""
    return {"dimensions": _bullets(run_mf("list", "dimensions", "--metrics", metric)),
            "entities": _bullets(run_mf("list", "entities", "--metrics", metric))}


def metric_entities(metric):
    """Entities a metric can be filtered or grouped by, read from the manifest.

    Used for the access check: a row filter on product_group can only be
    applied to a metric whose every input carries product_group.
    """
    data = manifest()
    entities = {m["name"]: {e["name"] for e in m["entities"]} for m in data["semantic_models"]}
    return set.intersection(*(entities[leaf["semantic_model"]] for leaf in _leaves(metric, data)))


def agg_time_dimensions(metric):
    """The time dimensions a metric's dates apply to, e.g. {"accepted_date"} for
    order metrics or {"snapshot_date"} for stock, read from the manifest."""
    data = manifest()
    if metric not in {m["name"] for m in data["metrics"]}:
        return set()
    return {leaf["agg_time_dimension"] for leaf in _leaves(metric, data)}


def resolve_dimension(metric, name):
    """The name MetricFlow knows a plain dimension by, for this metric, or None.

    People (and the router) say `product`; the stock model calls its column
    `stock_product` and MetricFlow wants it qualified by the model's primary
    entity: `stock_snapshot__stock_product`, while order lines give
    `order_line_snapshot__product`. Entities, metric_time and names that are
    already qualified are returned unchanged.
    """
    if name in ENTITIES or "__" in name or name == "metric_time":
        return name
    data = manifest()
    if metric not in {m["name"] for m in data["metrics"]}:
        return None
    models = {m["name"]: m for m in data["semantic_models"]}
    for leaf in _leaves(metric, data):
        model = models[leaf["semantic_model"]]
        primary = next((e["name"] for e in model["entities"] if e["type"] == "primary"), None)
        for dimension in model["dimensions"]:
            if dimension["name"] == name or dimension["name"].endswith("_" + name):
                return f"{primary}__{dimension['name']}" if primary else dimension["name"]
    return None


def column_for(rows, name):
    """The CSV column that holds a group-by: MetricFlow adds the grain to a time
    dimension asked for without one (`ad_spend_snapshot__period_start` comes back
    as `ad_spend_snapshot__period_start__day`)."""
    keys = list(rows[0]) if rows else []
    if name in keys:
        return name
    return next((k for k in keys if k.startswith(name + "__")), None)


def _leaves(metric, data):
    """The simple metrics underneath a metric, as their aggregation settings.

    In dbt's latest YAML spec there are no measures: a simple metric carries
    its own aggregation (metric_aggregation_params: semantic model, agg, time
    dimension). Ratio, derived and cumulative metrics name other metrics.
    """
    by_name = {m["name"]: m for m in data["metrics"]}
    params = by_name[metric]["type_params"]
    if params.get("metric_aggregation_params"):
        return [params["metric_aggregation_params"]]
    inputs = [m["name"] for m in params.get("metrics") or []]
    inputs += [params[k]["name"] for k in ("numerator", "denominator") if params.get(k)]
    inputs += [((params.get("cumulative_type_params") or {}).get("metric") or {}).get("name")]
    return [leaf for name in inputs if name for leaf in _leaves(name, data)]


# --- Querying ---------------------------------------------------------------

def where_clause(name, value):
    """{"channel": "paid_search"} -> {{ Entity('channel') }} = 'paid_search'"""
    kind = "Entity" if name in ENTITIES else "Dimension"
    safe = str(value).replace("'", "''")
    return f"{{{{ {kind}('{name}') }}}} = '{safe}'"


def mf_arguments(metrics, group_by=(), where=(), start=None, end=None):
    """The `mf query` arguments for a request. start/end are whole days, inclusive."""
    arguments = ["query", "--metrics", ",".join(metrics)]
    if group_by:
        arguments += ["--group-by", ",".join(group_by), "--order", ",".join(group_by)]
    for clause in where:
        arguments += ["--where", clause]
    if start:
        arguments += ["--start-time", start]
    if end:
        arguments += ["--end-time", end]
    return arguments


_CACHE = {}
CACHE_ENABLED = True           # the live evaluation turns it off, so latency is honest


def query(metrics, group_by=(), where=(), start=None, end=None, with_total=True,
          allow_uncut=False):
    """`_query`, remembered for the life of the process.

    Every request must filter to one cutoff: the warehouse holds one copy of
    each fact per cutoff, so a query without one counts every fact once per
    cutoff (three times over). allow_uncut=True is only for showing that
    mistake (authoring/draft.py explains a failed gate with it).

    The key includes the project folder and the modification times of the
    manifest and the warehouse, so a rebuild (or a scratch copy) never gets a
    stale answer. Saves about 1.5 s per repeated `mf` call in tests and evals.
    """
    if not allow_uncut and not any(w.startswith("{{ Entity('cutoff') }} = '") for w in where):
        raise LayerError("No cutoff: filter to one cutoff, e.g. "
                         + where_clause("cutoff", "friday_review") + ". Without one, every fact "
                         "is counted once per cutoff.")
    stamp = tuple(p.stat().st_mtime_ns if p.exists() else 0 for p in (MANIFEST, WAREHOUSE))
    key = (str(DBT_DIR), stamp, tuple(metrics), tuple(group_by), tuple(where), start, end, with_total)
    result = _CACHE.get(key) if CACHE_ENABLED else None
    if result is None:
        result = _query(metrics, group_by, where, start, end, with_total)
        _CACHE[key] = result
    return json.loads(json.dumps(result))              # a copy: callers may edit it


def _query(metrics, group_by=(), where=(), start=None, end=None, with_total=True):
    """Run one request: the rows (`--csv`), the compiled SQL (`--explain`) and,
    when grouped and with_total, the ungrouped total (never added up in Python:
    a ratio or a stock position does not sum). A cumulative metric has no single
    total, so it is asked for with with_total=False.

    Returns {"rows": [...], "total": {...} or None, "sql": "...", "command": "mf query ..."}.
    The calls run one after another: mf opens the DuckDB file for writing,
    and DuckDB allows one writer at a time.
    """
    arguments = mf_arguments(metrics, group_by, where, start, end)
    with tempfile.TemporaryDirectory() as tmp:
        grouped, total = Path(tmp) / "grouped.csv", Path(tmp) / "total.csv"
        run_mf(*arguments, "--csv", str(grouped))
        sql = _sql_from_explain(run_mf(*arguments, "--explain"))
        rows = _read_csv(grouped)
        if group_by and with_total:
            run_mf(*mf_arguments(metrics, (), where, start, end), "--csv", str(total))
        totals = rows if not group_by else _read_csv(total) if with_total else []
    return {"rows": rows, "total": totals[0] if totals else None,
            "sql": sql, "command": printable("mf", *arguments)}


def _read_csv(path):
    if not path.exists():           # mf writes no file at all when a query returns no rows
        return []
    with path.open(encoding="utf-8") as f:
        return [{k: _number(v) for k, v in row.items()} for row in csv.DictReader(f)]


def printable(*parts):
    """A command line you can paste into a terminal."""
    return " ".join(f'"{p}"' if re.search(r"[\s{}']", p) else p for p in parts)


# --- Helpers ----------------------------------------------------------------

def _bullets(output):
    return [line[2:].strip() for line in output.splitlines() if line.startswith("• ")]


def _sql_from_explain(output):
    """`mf query --explain` prints a banner and then the SQL; keep the SQL."""
    match = re.search(r"SQL \(remove --explain.*?\):\s*\n(.*)", output, re.DOTALL)
    return (match.group(1) if match else output).strip()


def _last_error(output):
    lines = [line for line in output.splitlines()
             if line.strip() and "new version" not in line and "pip install" not in line
             and "Please update" not in line and "found a bug" not in line
             and "metricflow/issues" not in line and "Initiating query" not in line]
    return "\n".join(lines[-12:])[-1500:]


def _number(text):
    """CSV text to a number: '50.0' -> 50, '-70.0' -> -70, '' -> None."""
    if text in ("", None):
        return None
    try:
        number = float(text)
    except ValueError:
        return text
    return int(number) if number.is_integer() else round(number, 4)
