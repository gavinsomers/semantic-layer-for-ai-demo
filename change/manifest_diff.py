"""Diff two semantic manifests and classify every change (guide chapter 9).

    uv run python -m sl diff --base change/production_manifest.json
    uv run python -m sl diff --base origin/main          # a git ref: parsed in a worktree

dbt writes the semantic manifest to dbt/target/semantic_manifest.json on every
`dbt parse` or `dbt build`. Comparing the manifest of a branch with the one
in production shows what a pull request changes in MEANING, whatever the
YAML diff looks like. Each metric is reduced to five parts:

    formula   type, expression, inputs, and each simple metric's agg and expr
              (dbt's latest spec: simple metrics carry their own aggregation;
              a legacy manifest's measures are read the same way)
    filter    every filter on the metric or its inputs
    grain     the semantic models underneath: primary entity, entities,
              dimensions, time dimension
    selection the meta keys that change answers: defaults (cutoff, dates,
              filters a question leaves open), cannot_group_by, hidden, sensitive
    metadata  label, description and the rest of meta (owner, status, version...)

and each change is one of:

    added, removed, rename, formula, filter, grain, selection, metadata-only

A change is BREAKING when an existing, non-draft metric would give a different
number (or a refusal instead of a number), or stop existing, for the same
request: removed, rename, formula, filter, grain and selection. The fix for a breaking change is not to edit the metric in
place but to add a new version beside it (contribution_after_ads_v2) and
deprecate the old one: that is added + metadata-only, which is not breaking.
Draft metrics may change freely: nobody may rely on a draft.
"""

import json
from pathlib import Path

BREAKING = {"removed", "rename", "formula", "filter", "grain", "selection"}
# Meta keys that change what a request returns, so they are not "metadata only".
SELECTION_KEYS = ("defaults", "cannot_group_by", "hidden", "sensitive")


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _canon(value):
    """A stable string for comparing nested dicts and lists."""
    return json.dumps(value, sort_keys=True, default=str)


def parts(manifest):
    """{metric name: {"formula", "filter", "grain", "metadata", "status"}} for one manifest."""
    metrics = {m["name"]: m for m in manifest["metrics"]}
    measures, models = {}, {}
    for model in manifest["semantic_models"]:
        primary = [e for e in model["entities"] if e["type"] == "primary"]
        models[model["name"]] = {
            "relation": model["node_relation"]["alias"],
            "primary": [(e["name"], e.get("expr")) for e in primary],
            "entities": sorted((e["name"], e["type"], e.get("expr")) for e in model["entities"]),
            "dimensions": sorted((d["name"], d["type"], d.get("expr"),
                                  (d.get("type_params") or {}).get("time_granularity"))
                                 for d in model.get("dimensions") or []),
            "agg_time": (model.get("defaults") or {}).get("agg_time_dimension"),
        }
        for measure in model.get("measures") or []:          # legacy spec only
            measures[measure["name"]] = {
                "model": model["name"], "agg": measure["agg"], "expr": measure.get("expr"),
                "agg_params": measure.get("agg_params"),
                "non_additive": measure.get("non_additive_dimension"),
                "agg_time": measure.get("agg_time_dimension")}

    def aggregation(p):
        """A simple metric's aggregation: on the metric (latest spec) or its measure (legacy)."""
        own = p.get("metric_aggregation_params")
        if own:
            return {"model": own["semantic_model"], "agg": own["agg"], "expr": p.get("expr"),
                    "agg_params": own.get("agg_params"),
                    "non_additive": own.get("non_additive_dimension"),
                    "agg_time": own.get("agg_time_dimension")}
        if p.get("measure"):
            return measures[p["measure"]["name"]] | {"name": p["measure"]["name"]}
        return None

    def inputs(metric):
        p = metric["type_params"]
        refs = list(p.get("metrics") or [])
        refs += [p[k] for k in ("numerator", "denominator") if p.get(k)]
        if (p.get("cumulative_type_params") or {}).get("metric"):
            refs.append(p["cumulative_type_params"]["metric"])
        return refs

    def formula(name):
        if name not in metrics:
            return {"missing input": name}        # a dangling reference after a removal
        m, p = metrics[name], metrics[name]["type_params"]
        simple = aggregation(p)
        own = {"type": m["type"], "expr": p.get("expr"), "window": p.get("window"),
               "grain_to_date": p.get("grain_to_date"),
               "cumulative": p.get("cumulative_type_params"),
               "aggregation": {k: v for k, v in simple.items() if k != "model"} if simple else None,
               "inputs": [{"name": r["name"], "alias": r.get("alias"),
                           "offset": r.get("offset_window")} for r in inputs(m)]}
        return {"own": own, "inputs": {r["name"]: formula(r["name"]) for r in inputs(m)}}

    def filters(name):
        if name not in metrics:
            return {"missing input": name}
        m, p = metrics[name], metrics[name]["type_params"]
        own = [m.get("filter"), (p.get("measure") or {}).get("filter")]
        own += [r.get("filter") for r in inputs(m)]
        return {"own": own, "inputs": {r["name"]: filters(r["name"]) for r in inputs(m)}}

    def used_models(name):
        if name not in metrics:
            return set()
        simple = aggregation(metrics[name]["type_params"])
        if simple:
            return {simple["model"]}
        return set().union(*(used_models(r["name"]) for r in inputs(metrics[name])))

    def grain(name):
        return {model: models[model] for model in sorted(used_models(name)) if model in models}

    def meta(m):
        return (m.get("config") or {}).get("meta") or {}

    return {name: {"formula": _canon(formula(name)), "filter": _canon(filters(name)),
                   "selection": _canon({k: meta(m).get(k) for k in SELECTION_KEYS}),
                   "grain": json.loads(_canon(grain(name))),
                   "metadata": _canon({"label": m.get("label"), "description": m.get("description"),
                                       "meta": (m.get("config") or {}).get("meta")}),
                   "status": ((m.get("config") or {}).get("meta") or {}).get("status", "draft"),
                   "version": ((m.get("config") or {}).get("meta") or {}).get("version")}
            for name, m in metrics.items()}


def diff(base, head):
    """Every change from base to head, as a list of dicts:
    {"metric", "kind", "breaking", "detail"}."""
    old, new = parts(base), parts(head)
    changes = []
    removed = [n for n in old if n not in new]
    added = [n for n in new if n not in old]

    # A rename is a removal and an addition with the same meaning.
    for name in list(removed):
        same = [a for a in added if old[name]["formula"] == new[a]["formula"]
                and old[name]["filter"] == new[a]["filter"] and old[name]["grain"] == new[a]["grain"]]
        if same:
            removed.remove(name)
            added.remove(same[0])
            changes.append(_change(name, "rename", old[name], f"renamed to {same[0]}"))
    for name in removed:
        changes.append(_change(name, "removed", old[name], "no longer in the manifest"))
    for name in added:
        changes.append(_change(name, "added", new[name], f"new {new[name]['status']} metric"))

    for name in sorted(set(old) & set(new)):
        kinds = [k for k in ("formula", "filter", "selection") if old[name][k] != new[name][k]]
        if not grain_kept(old[name]["grain"], new[name]["grain"]):
            kinds.append("grain")
        for kind in kinds:
            changes.append(_change(name, kind, old[name], _explain(kind)))
        if not kinds and old[name]["metadata"] != new[name]["metadata"]:
            detail = "label, description or meta"
            if old[name]["status"] != new[name]["status"]:
                detail = f"status {old[name]['status']} -> {new[name]['status']}"
            changes.append(_change(name, "metadata-only", old[name], detail))
    return changes


def grain_kept(old, new):
    """Same table, primary entity and time dimension, and nothing taken away.
    Adding a dimension or entity is not a change of grain: no request breaks."""
    for model, before in old.items():
        after = new.get(model)
        if after is None or any(after[k] != before[k] for k in ("relation", "primary", "agg_time")):
            return False
        for k in ("entities", "dimensions"):
            if not all(item in after[k] for item in before[k]):
                return False
    return True


def _change(name, kind, before, detail):
    # Breaking only for a metric someone may rely on: anything not a draft.
    breaking = kind in BREAKING and before["status"] != "draft"
    return {"metric": name, "kind": kind, "breaking": breaking, "detail": detail,
            "status_before": before["status"], "version_before": before["version"]}


def _explain(kind):
    return {"formula": "the calculation changed (expression, inputs, or a simple metric's agg or expr)",
            "filter": "a filter on the metric or one of its inputs changed",
            "grain": "a semantic model underneath changed its entities, dimensions or time",
            "selection": "a default, allowed breakdown or visibility in meta changed"}[kind]


def report(changes):
    """Lines for a person (and a CI log)."""
    if not changes:
        return ["No changes to any metric."]
    lines = []
    for c in sorted(changes, key=lambda c: (not c["breaking"], c["metric"])):
        flag = "BREAKING" if c["breaking"] else "ok      "
        lines.append(f"  {flag} {c['kind']:14} {c['metric']:30} {c['detail']}")
    return lines
