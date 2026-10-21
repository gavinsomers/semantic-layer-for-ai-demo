"""Breaking-change gate and the chapter 9 demo.

    uv run python -m sl diff [--base PATH_OR_GIT_REF]    the gate CI runs
    uv run python -m sl change-demo                      change a formula, then do it properly
    uv run python -m sl release                          record what is in production

The base is what is in production: by default change/production_manifest.json
(written by `sl release` when a change ships), or the manifest of a git ref
such as origin/main, parsed in a temporary worktree.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

import yaml

from change import impact, manifest_diff

ROOT = Path(__file__).resolve().parent.parent
DBT_DIR = ROOT / "dbt"
PRODUCTION = ROOT / "change" / "production_manifest.json"
SEMANTIC = Path("models") / "semantic" / "semantic_layer.yml"


def dbt(project, *arguments):
    exe = shutil.which("dbt", path=str(Path(sys.executable).parent)) or "dbt"
    done = subprocess.run([exe, *arguments, "--project-dir", str(project), "--profiles-dir",
                           str(project)], cwd=project, capture_output=True, text=True,
                          encoding="utf-8", env={**os.environ, "PYTHONUTF8": "1"})
    if done.returncode != 0:
        raise RuntimeError(f"dbt {' '.join(arguments)} failed:\n{done.stdout[-2000:]}")
    return done.stdout


def parse(project):
    """`dbt parse` a project and return its semantic manifest."""
    dbt(project, "parse")
    return manifest_diff.load(Path(project) / "target" / "semantic_manifest.json")


def base_manifest(base=None):
    """The production manifest: a JSON file, or a git ref parsed in a worktree."""
    base = base or str(PRODUCTION)
    if Path(base).exists():
        return manifest_diff.load(base)
    with tempfile.TemporaryDirectory() as tmp:
        worktree = Path(tmp) / "base"
        subprocess.run(["git", "worktree", "add", "--detach", str(worktree), base], cwd=ROOT,
                       check=True, capture_output=True)
        try:
            return parse(worktree / "dbt")
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=ROOT,
                           capture_output=True)


def gate(base, head, root=ROOT, allow_breaking=False):
    """Diff, impact and verdict. Returns (exit code, lines to print)."""
    changes = manifest_diff.diff(base, head)
    lines = manifest_diff.report(changes)
    breaking = [c["metric"] for c in changes if c["breaking"]]
    touched = [c["metric"] for c in changes if c["kind"] != "added"]
    if touched:
        lines += ["", "  Impact of the changed metrics:"]
        lines += impact.report(impact.impact(base, touched, root))   # they all exist in base
    if breaking and not allow_breaking:
        lines += ["", f"  GATE: FAIL. {len(breaking)} breaking change(s) to metrics people rely on.",
                  "  Ship the new meaning as a new version beside the old one, deprecate",
                  "  the old one, and re-run the affected verified queries and eval cases.",
                  "  (A business owner can override with the breaking-change-approved label.)"]
        return 1, lines
    lines += ["", "  GATE: PASS. No breaking change." if not breaking else
              "  GATE: PASS (breaking change approved)."]
    return 0, lines


def release(_args=None, today=None):
    """Record the current manifest as what is in production (a deploy step), and
    append what changed, classified, with any deprecation notice, to CHANGELOG.md."""
    current = DBT_DIR / "target" / "semantic_manifest.json"
    head = manifest_diff.load(current)
    changes = manifest_diff.diff(base_manifest(), head) if PRODUCTION.exists() else []
    PRODUCTION.write_text(json.dumps(head, indent=1, sort_keys=True), encoding="utf-8")
    print(f"Recorded {current.relative_to(ROOT)} as {PRODUCTION.relative_to(ROOT)}")
    if changes:
        changelog = ROOT / "CHANGELOG.md"
        entry = changelog_entry(changes, head, today or date.today().isoformat())
        old = changelog.read_text(encoding="utf-8") if changelog.exists() else "# Changelog\n"
        changelog.write_text(old.rstrip("\n") + "\n\n" + entry, encoding="utf-8")
        print(f"Added {len(changes)} change(s) to {changelog.relative_to(ROOT)}")
    return 0


def changelog_entry(changes, head, day):
    """A CHANGELOG.md section from the classified diff and the deprecation notices."""
    notices = {m["name"]: ((m.get("config") or {}).get("meta") or {}).get("deprecation") or {}
               for m in head["metrics"]}
    lines = [f"## {day}", ""]
    for c in sorted(changes, key=lambda c: (not c["breaking"], c["metric"])):
        lines.append(f"- {'BREAKING ' if c['breaking'] else ''}{c['kind']}: `{c['metric']}`, {c['detail']}")
        notice = " ".join(str(notices.get(c["metric"], {}).get("notice", "")).split())
        if notice:
            lines.append(f"  Notice: {notice}")
    return "\n".join(lines) + "\n"


# --- The demo -------------------------------------------------------------------

# The wrong way: change version 1's refund rule in place.
IN_PLACE = ("        expr: contribution_before_ads_paid_rule",
            "        expr: contribution_before_ads_approved_rule")

# The right way: version 2 is certified and version 1 is deprecated, with a
# sunset date and a notice every consumer passes on. It models the book's
# "given notice" route (chapter 8): announced on Friday 2 October 2026,
# version 2 answers current questions from 00:00 on Monday 5 October 2026.
V1_META = ('        status: certified\n        version: 1\n        last_reviewed: "2026-10-02"\n'
           '        unit: GBP\n        refund_rule: paid-refund rule\n')
V1_DEPRECATED = (
    '        status: deprecated\n        version: 1\n        last_reviewed: "2026-10-02"\n'
    '        unit: GBP\n        refund_rule: paid-refund rule\n'
    '        deprecation:\n'
    '          deprecated_on: "2026-10-02"\n'
    '          sunset: "2026-10-05"            # 00:00: not used for current answers from then\n'
    '          replaced_by: contribution_after_ads_v2\n'
    '          route: given notice\n'
    '          removal: decided by the head of data on the usage check\n'
    '          notice: >\n'
    '            Notice given on Friday 2 October 2026: from 00:00 on Monday 5 October\n'
    '            2026, current contribution answers use version 2 (approved-refund rule).\n'
    '            Version 1 (paid-refund rule) stays available to reproduce reports made\n'
    "            under it, such as the Friday review's £50.\n")
V2_META = ('        status: draft\n        version: 2\n        last_reviewed: "2026-10-02"\n'
           '        effective_from: "2026-10-05"\n')
V2_CERTIFIED = V2_META.replace("status: draft", "status: certified")

# The descriptions a model reads change with the status: each says which rule it is.
V1_DESCRIPTION = ("      An approved but unpaid refund does NOT count. The rule in force.\n",
                  "      An approved but unpaid refund does NOT count. Deprecated: the rule in\n"
                  "      force until 00:00 on 5 October 2026; use it to reproduce reports made\n"
                  "      under it, such as the Friday review.\n")
V2_DESCRIPTION = ("      approved by the cutoff, paid or not; each refund once. NOT the rule in\n"
                  "      force before its effective_from date (in its metadata): until then, use\n"
                  "      only when someone asks what counting approved refunds would do, and label\n"
                  "      the answer a comparison.\n",
                  "      approved by the cutoff, paid or not; each refund once. The rule in force\n"
                  "      for current answers from 00:00 on 5 October 2026. For reports made\n"
                  "      under the paid-refund rule (the Friday review), use contribution_after_ads.\n")

# Context that names the current rule moves to version 2; the Friday review's
# phrasings stay on version 1, which still reproduces that report.
CURRENT_SYNONYMS = ["contribution", "contribution after advertising", "provisional contribution"]
CURRENT_PHRASING = "What is our paid-search contribution, and does it include the refund we have promised?"


def scratch_copy(tmp):
    """dbt/ plus the files impact.py reads, so context can change in the copy too."""
    scratch = Path(tmp) / "dbt"
    shutil.copytree(DBT_DIR, scratch, ignore=shutil.ignore_patterns("target", "logs"))
    shutil.copytree(ROOT / "context", Path(tmp) / "context")
    for name in ("evals/cases.yml", "change/consumers.yml"):
        (Path(tmp) / name).parent.mkdir(exist_ok=True)
        shutil.copy(ROOT / name, Path(tmp) / name)
    return scratch


def move_current_context(root):
    """Point the current-rule synonyms and verified phrasing at version 2.
    Returns lines saying what moved."""
    synonyms_path = root / "context" / "synonyms.yml"
    synonyms = yaml.safe_load(synonyms_path.read_text(encoding="utf-8"))
    words = synonyms["metrics"]
    words["contribution_after_ads"] = [w for w in words["contribution_after_ads"]
                                       if w not in CURRENT_SYNONYMS] + ["paid-refund rule"]
    words["contribution_after_ads_v2"] = CURRENT_SYNONYMS + words["contribution_after_ads_v2"]
    synonyms_path.write_text(yaml.safe_dump(synonyms, sort_keys=False), encoding="utf-8")

    vq_path = root / "context" / "verified_queries.yml"
    vqs = yaml.safe_load(vq_path.read_text(encoding="utf-8"))
    by_id = {vq["id"]: vq for vq in vqs["verified_queries"]}
    friday = by_id["vq_contribution_friday_review"]
    friday["questions"].remove(CURRENT_PHRASING)
    by_id["vq_approved_refund_comparison"]["category"] = "supported"   # no longer a comparison
    vqs["verified_queries"].append({
        **friday, "id": "vq_contribution_current", "questions": [CURRENT_PHRASING],
        "request": {**friday["request"], "metric": "contribution_after_ads_v2"},
        "expected": dict(by_id["vq_approved_refund_comparison"]["expected"])})
    vq_path.write_text(yaml.safe_dump(vqs, sort_keys=False), encoding="utf-8")
    return [f"synonyms {', '.join(CURRENT_SYNONYMS)}: contribution_after_ads -> contribution_after_ads_v2",
            f"verified query vq_contribution_current (new, v2): \"{CURRENT_PHRASING}\"",
            "vq_approved_refund_comparison: constrained -> supported (version 2 is the rule now)",
            "kept on v1: vq_contribution_friday_review, with the Friday review's phrasings"]


def edit(project, *replacements):
    path = project / SEMANTIC
    text = path.read_text(encoding="utf-8")
    for old, new in replacements:
        assert old in text, f"could not find: {old[:60]}"
        text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")


def change_demo(_args=None):
    from ci import regression
    print("Base: the semantic manifest of the current project (as in production).\n", flush=True)
    base = manifest_diff.load(DBT_DIR / "target" / "semantic_manifest.json")

    print("=" * 72 + "\n1. The wrong way: change version 1's refund rule in place\n" + "=" * 72)
    print(f"   {IN_PLACE[0].strip()}  ->  {IN_PLACE[1].strip()}\n", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        scratch = scratch_copy(tmp)
        edit(scratch, IN_PLACE)
        code, lines = gate(base, parse(scratch))
        print("\n".join(lines))
        print("\n   The metric regression test on frozen data agrees:", flush=True)
        dbt(scratch, "build")
        for line in regression.check(scratch, only=["contribution_after_ads"]):
            print("   " + line)
    wrong_way_failed = code == 1

    print("\n" + "=" * 72 + "\n2. The right way: certify version 2 beside version 1, deprecate v1\n"
          + "=" * 72, flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        scratch = scratch_copy(tmp)
        edit(scratch, (V1_META, V1_DEPRECATED), (V2_META, V2_CERTIFIED), V1_DESCRIPTION, V2_DESCRIPTION)
        moved = move_current_context(Path(tmp))
        code, lines = gate(base, parse(scratch), root=Path(tmp))
        print("\n".join(lines))
        print("\n   Context moved with the meaning (scratch copy):")
        for line in moved:
            print(f"   {line}")
        print("\n   What the assistant now says when version 1 answers (scratch copy):", flush=True)
        dbt(scratch, "build")
        answer = regression.ask_in(scratch, "contribution_after_ads", ["product_group"])
        print(f"   {answer['answer_text']}")
        for caveat in answer["caveats"]:
            print(f"   Caveat: {caveat}")
        print(f"   Lineage: {answer['lineage'][0]['metric']} v{answer['lineage'][0]['version']}, "
              f"{answer['lineage'][0]['status']}")
    right_way_passed = code == 0
    print("\nThe real project was not touched.")
    return 0 if wrong_way_failed and right_way_passed else 1
