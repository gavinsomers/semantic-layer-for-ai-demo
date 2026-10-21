"""What a metric change touches (guide chapter 9).

Given the metrics a pull request changes, list everything that depends on
them, directly or through a derived metric:

    verified queries    context/verified_queries.yml (their answers may move)
    eval cases          evals/cases.yml (their expected values may move)
    consumers           change/consumers.yml (dashboards, agents, packs to tell)

Run by `sl diff` and `sl change-demo` after the manifest diff.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def dependants(manifest, names):
    """The changed metrics plus every metric built on them (derived, ratio...)."""
    uses = {}
    for m in manifest["metrics"]:
        p = m["type_params"]
        refs = [r["name"] for r in p.get("metrics") or []]
        refs += [p[k]["name"] for k in ("numerator", "denominator") if p.get(k)]
        refs += [((p.get("cumulative_type_params") or {}).get("metric") or {}).get("name")]
        uses[m["name"]] = set(refs) - {None}
    found, frontier = set(names), set(names)
    while frontier:
        frontier = {m for m, refs in uses.items() if refs & frontier} - found
        found |= frontier
    return found


def impact(manifest, changed, root=ROOT):
    """{"metrics": [...], "verified_queries": [...], "eval_cases": [...], "consumers": [...]}"""
    affected = dependants(manifest, changed)
    vqs = yaml.safe_load((root / "context" / "verified_queries.yml").read_text(encoding="utf-8"))
    cases = yaml.safe_load((root / "evals" / "cases.yml").read_text(encoding="utf-8"))["cases"]
    consumers = yaml.safe_load((root / "change" / "consumers.yml").read_text(encoding="utf-8"))
    return {
        "metrics": sorted(affected),
        "verified_queries": [vq["id"] for vq in vqs["verified_queries"]
                             if (vq.get("request") or {}).get("metric") in affected],
        "eval_cases": [c["id"] for c in cases if c["expect"].get("metric") in affected],
        "consumers": [f"{c['id']} ({c['kind']} {c['surface']}, owner {c['owner']}"
                      + (f", pinned until {c['pinned_until']}" if c.get("pinned_until") else "") + ")"
                      for c in consumers["consumers"] if set(c["metrics"]) & affected],
    }


def report(found):
    lines = [f"  Metrics affected:   {', '.join(found['metrics']) or 'none'}"]
    for key, label in (("verified_queries", "Verified queries"), ("eval_cases", "Eval cases"),
                       ("consumers", "Consumers to tell")):
        lines.append(f"  {label + ':':19} " + (", ".join(found[key]) or "none"))
    return lines
