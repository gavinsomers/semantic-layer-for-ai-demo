"""AI-drafted semantic models, and the gates between a draft and a merge.

Guide chapter 5.

    uv run python -m sl draft --offline     # canned drafts: round 1 fails a gate, round 2 passes
    uv run python -m sl draft               # a live model drafts, a second call critiques
    uv run python -m sl approve-draft authoring/scratch/refunds.yml --reviewer "finance lead"

An agent can write a plausible semantic model in seconds. Nothing it writes
reaches dbt/models/semantic/ until it passes, in order:

(The draft is in dbt's latest YAML spec: the mart's own `models:` entry, with
`semantic_model:`, entities and dimensions on its columns, simple metrics and
the mart's column tests. So a merged draft replaces dbt/models/marts/fct_refunds.yml.)


    1. parse       dbt parse, then mf validate-configs (including warehouse checks)
    2. questions   the sample questions in authoring/sample_questions.yml give
                   their expected figures through MetricFlow
    3. critic      a second pass against authoring/instructions.md: offline, a
                   fixed checklist; live, a second model call
    4. person      a named reviewer approves; the reviewed file's hash goes in
                   the lockfile (authoring/lock.py), so later edits need review too.
                   `sl approve-draft` refuses a draft whose recorded gate result
                   (refunds.gates.json, beside it) is not a pass for this exact
                   text, unless --override. Approval sets approver and last_reviewed.

A failed gate goes back to the drafter with the reason. At most two rounds,
then a person decides, whatever state the draft is in.
"""

import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path

import yaml

from serving import layer

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
SCRATCH = HERE / "scratch"
MART = "fct_refunds"
MART_PROPERTIES = Path("models") / "marts" / f"{MART}.yml"   # replaced by the draft
MAX_ROUNDS = 2


class NotApproved(Exception):
    """The draft did not pass its gates, so it cannot be merged without --override."""


# --- The gates -------------------------------------------------------------------

def gate_parse(scratch, text=""):
    from change.demo import dbt
    try:
        draft = yaml.safe_load(text) if text.strip() else None
    except yaml.YAMLError as error:
        return False, f"the draft is not valid YAML: {str(error).splitlines()[0]}"
    entry = _entry(draft)
    if not entry or not entry.get("semantic_model") or not entry.get("metrics"):
        return False, (f"the draft is not YAML with a models entry for {MART} that has a "
                       "semantic_model and a metrics list")
    unknown = unknown_keys(entry)
    try:
        dbt(scratch, "parse")
    except RuntimeError as error:
        # dbt 1.12 dumps the whole metrics block ("... is not valid under any of
        # the given schemas") without naming the key: name it for the drafter.
        return False, "; ".join(unknown + [_tail(str(error))])
    if unknown:
        return False, "; ".join(unknown)
    # mf validate-configs prints "ERRORS: 0" on success, so read the counts, not the word.
    done = subprocess.run([layer.tool("mf"), "validate-configs"], cwd=scratch, capture_output=True,
                          text=True, encoding="utf-8", env={**os.environ, "PYTHONUTF8": "1"})
    output = done.stdout + done.stderr
    if done.returncode != 0 or re.search(r"ERRORS: [1-9]", output):
        return False, "mf validate-configs: " + _tail(output, 6)
    return True, "dbt parse and mf validate-configs (with warehouse checks) passed"


# Keys dbt 1.12 accepts on a latest-spec simple metric (UnparsedMetricV2) and on
# its non_additive_dimension. Legacy-spec names get a pointer to the new name.
METRIC_KEYS = {"name", "type", "label", "description", "filter", "time_granularity", "config",
               "hidden", "agg", "percentile", "percentile_type", "join_to_timespine",
               "fill_nulls_with", "expr", "non_additive_dimension", "agg_time_dimension"}
NON_ADDITIVE_KEYS = {"name", "window_agg", "group_by"}
LEGACY = {"window_choice": "window_agg", "window_groupings": "group_by",
          "type_params": "agg and expr on the metric itself", "measure": "agg and expr",
          "meta": "config: meta:"}


def unknown_keys(entry):
    """Problems a drafter can act on: each key the latest spec does not know, by name."""
    problems = []

    def name(key):
        return f"'{key}' (latest spec: {LEGACY[key]})" if key in LEGACY else f"'{key}'"

    for metric in entry.get("metrics") or []:
        if not isinstance(metric, dict):
            continue
        label = metric.get("name", "a metric")
        extra = [k for k in metric if k not in METRIC_KEYS]
        if extra:
            problems.append(f"{label}: unknown key(s) {', '.join(map(name, extra))}")
        nad = metric.get("non_additive_dimension")
        if nad is None:
            continue
        if not isinstance(nad, dict):
            problems.append(f"{label}: non_additive_dimension must be a mapping "
                            "{name, window_agg, group_by}, not " + repr(nad))
            continue
        extra = [k for k in nad if k not in NON_ADDITIVE_KEYS]
        if extra:
            problems.append(f"{label}: non_additive_dimension has unknown key(s) "
                            f"{', '.join(map(name, extra))}; it takes name, window_agg, group_by")
        missing = [k for k in ("name", "window_agg") if k not in nad]
        if missing:
            problems.append(f"{label}: non_additive_dimension lacks {', '.join(missing)}")
    return problems


def gate_questions(scratch, spec=None):
    spec = spec or yaml.safe_load((HERE / "sample_questions.yml").read_text(encoding="utf-8"))
    problems, passed = [], 0
    with _project(scratch):
        for q in spec["questions"]:
            where = [layer.where_clause(k, v) for k, v in q["where"].items()]
            try:
                total = layer.query([q["metric"]], (), where)["total"][q["metric"]]
            except (layer.LayerError, KeyError, TypeError) as error:
                problems.append(f"'{q['question']}': " + _explain_failure(q, error))
                continue
            if total is None or abs(total - q["expected"]) > 0.005:
                problems.append(f"'{q['question']}': expected {q['expected']}, got {total}")
            else:
                passed += 1
        if problems:
            return False, "; ".join(problems)
        # A wrong non_additive_dimension on an additive amount keeps only the
        # latest date, so it passes any question whose rows share one date.
        # Any one spanning question will do; the last ones are the likeliest.
        spans = next((n for n in map(_dates, reversed(spec["questions"])) if n > 1), 0)
    if not spans:
        return False, (f"all {passed} sample questions give their expected figures, but each "
                       "figure comes from a single date, so a metric that keeps only the latest "
                       "date would pass: add a sample question spanning several dates")
    return True, (f"all {passed} sample questions give their expected figures, "
                  f"one spanning {spans} dates")


def _dates(q):
    """How many days contribute to a sample question's figure (by metric_time)."""
    where = [layer.where_clause(k, v) for k, v in q["where"].items()]
    rows = layer.query([q["metric"]], ("metric_time__day",), where, with_total=False)["rows"]
    return sum(1 for row in rows if row.get(q["metric"]))


def checklist(draft):
    """The offline critic: the instructions as rules a script can check."""
    issues = []
    model = _entry(draft) or {}
    name = (model.get("semantic_model") or {}).get("name") or model.get("name", MART)
    entities = {(c.get("entity") or {}).get("name") or c["name"]: {**c["entity"], "column": c["name"]}
                for c in model.get("columns") or [] if c.get("entity")}
    primary = [e for e in entities.values() if e.get("type") == "primary"]
    if len(primary) != 1:
        issues.append(f"{name}: needs exactly one primary entity (rule 1)")
    elif primary[0]["column"] != "snapshot_key":
        issues.append(f"{name}: the primary entity must be snapshot_key, one row "
                      "per thing per cutoff (rule 1)")
    if "cutoff" not in entities:
        issues.append(f"{name}: no cutoff entity, so it cannot be limited to one cutoff (rule 2)")
    if not model.get("agg_time_dimension"):
        issues.append(f"{name}: no default time dimension (rule 4)")
    for metric in model.get("metrics") or []:
        meta = (metric.get("config") or {}).get("meta") or {}
        missing = [k for k in ("owner", "approver", "status", "version", "last_reviewed")
                   if k not in meta]
        if missing:
            issues.append(f"{metric['name']}: meta lacks {', '.join(missing)} (rule 7)")
        if meta.get("status") != "draft":
            issues.append(f"{metric['name']}: a new metric must be status: draft (rule 7)")
        if meta.get("last_reviewed") is not None:
            issues.append(f"{metric['name']}: a draft has not been reviewed, so last_reviewed "
                          "must be null until a person approves it (rule 7)")
        if "Not for" not in (metric.get("description") or ""):
            issues.append(f"{metric['name']}: the description has no 'Not for:' (rule 6)")
    return issues


def _entry(draft):
    """The draft's models entry for the mart, or None."""
    models = draft.get("models") if isinstance(draft, dict) else None
    return next((m for m in models or [] if isinstance(m, dict) and m.get("name") == MART), None)


def gate_critic(draft_text, live):
    draft = yaml.safe_load(draft_text) or {}
    issues = checklist(draft)
    if live:
        issues += critic_model(draft_text)
    return (not issues), ("; ".join(issues) if issues else "no issues against the instructions")


# --- The model calls (live mode) --------------------------------------------------

def draft_prompt(feedback=None):
    import duckdb
    with duckdb.connect(str(layer.WAREHOUSE), read_only=True) as con:
        columns = con.execute("select column_name, data_type from information_schema.columns "
                              "where table_name = ? order by ordinal_position", [MART]).fetchall()
    sql = (ROOT / "dbt" / "models" / "marts" / f"{MART}.sql").read_text(encoding="utf-8")
    example = (ROOT / "dbt" / "models" / "semantic" / "semantic_layer.yml").read_text(encoding="utf-8")
    example = example[example.index("  - name: fct_cash_movements"):
                      example.index("\n# Metrics built from other metrics.")]
    tests = (ROOT / "dbt" / MART_PROPERTIES).read_text(encoding="utf-8")
    prompt = (f"Draft a dbt MetricFlow semantic model for the mart {MART}, in dbt's latest YAML "
              "spec, with two simple metrics: refunds_outstanding (approved but not yet paid) "
              "and refunds_approved (approved, paid or not). Keep the mart's column tests, "
              f"shown below: the draft replaces {MART_PROPERTIES.as_posix()}.\n\n" + (HERE / "instructions.md").read_text(encoding="utf-8")
              + f"\n\nColumns of {MART}:\n" + "\n".join(f"- {c} {t}" for c, t in columns)
              + f"\n\nThe mart's SQL:\n{sql}\n\nIts current properties file:\n{tests}"
              + "\n\nTwo existing semantic models in this project, in the latest spec, "
              f"for style (cash is additive; stock is a position):\n{example}"
              + LATEST_SPEC_NOTES)
    if feedback:
        prompt += f"\n\nYour previous draft failed review. Fix these problems:\n{feedback}"
    return prompt


LATEST_SPEC_NOTES = """
Every key of a latest-spec non_additive_dimension, for a position such as a
balance or stock (the window is taken per group_by entity):

        non_additive_dimension:
          name: snapshot_date        # the time dimension the position is read on
          window_agg: max            # max: the last date in the window; min: the first
          group_by: [product_group]  # optional: entities that each take their own last date

The legacy keys window_choice and window_groupings, and a bare dimension name,
are not valid in the latest spec. Refund amounts are additive (they sum across
refunds and dates, like cash): refund metrics need no non_additive_dimension.
"""


def ask_model(prompt):
    from serving import llm
    client, model = llm.connect()
    # A reasoning model can think for tens of thousands of tokens before it writes.
    choice = client.chat.completions.create(
        model=model, messages=[{"role": "user", "content": prompt}], max_tokens=32000,
        temperature=0).choices[0]
    if choice.finish_reason == "length" and not choice.message.content:
        return "# The model ran out of tokens before writing a draft.\n"
    return (choice.message.content or "").strip()


def draft_model(feedback=None):
    text = ask_model(draft_prompt(feedback))
    match = re.search(r"```(?:ya?ml)?\s*\n(.*?)```", text, re.DOTALL)
    return (match.group(1) if match else text).strip() + "\n"


def critic_model(draft_text):
    """A second, separate call: review the draft against the instructions only."""
    reply = ask_model(
        "You review semantic model drafts. Check this draft against the instructions and "
        "reply with JSON only: {\"approve\": true or false, \"issues\": [\"...\"]}. List only "
        "real breaches of a numbered instruction.\n\nInstructions:\n"
        + (HERE / "instructions.md").read_text(encoding="utf-8") + f"\n\nDraft:\n{draft_text}")
    match = re.search(r"\{.*\}", reply, re.DOTALL)
    try:
        verdict = json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        verdict = {}
    if verdict.get("approve") is True:
        return []
    return [f"critic: {i}" for i in verdict.get("issues") or ["no clear verdict"]]


# --- The loop ---------------------------------------------------------------------

def run(live=False, out=None):
    """Draft, gate, revise (at most two rounds), then hand to a person.
    Returns {"rounds": [...], "draft": path, "ready_for_review": bool}."""
    out = Path(out or SCRATCH / "refunds.yml")
    rounds, feedback = [], None
    for number in range(1, MAX_ROUNDS + 1):
        if live:
            text = draft_model(feedback)
        else:
            text = (HERE / "canned" / f"refunds_round{number}.yml").read_text(encoding="utf-8")
        gates = []
        with scratch_project(text) as scratch:
            for name, check in (("parse", lambda: gate_parse(scratch, text)),
                                ("questions", lambda: gate_questions(scratch)),
                                ("critic", lambda: gate_critic(text, live))):
                ok, detail = check()
                gates.append({"gate": name, "passed": ok, "detail": detail})
                if not ok:
                    break
        rounds.append({"round": number, "gates": gates})
        if all(g["passed"] for g in gates):
            break
        feedback = gates[-1]["detail"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    passed = all(g["passed"] for g in rounds[-1]["gates"]) and len(rounds[-1]["gates"]) == 3
    record_gates(out, rounds, passed)
    return {"rounds": rounds, "draft": out, "ready_for_review": passed}


@contextlib.contextmanager
def scratch_project(text):
    """A throwaway copy of the project (and warehouse) with the draft in place."""
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp) / "dbt"
        shutil.copytree(ROOT / "dbt", scratch, ignore=shutil.ignore_patterns("target", "logs"))
        shutil.copy(layer.WAREHOUSE, Path(tmp) / "warehouse.duckdb")
        (scratch / MART_PROPERTIES).unlink()          # the draft is the mart's whole entry
        (scratch / "models" / "semantic" / "draft_refunds.yml").write_text(text, encoding="utf-8")
        yield scratch


def record_gates(draft, rounds, passed):
    """Write the gate result beside the draft, with the hash of the text it is for."""
    Path(draft).with_suffix(".gates.json").write_text(json.dumps(
        {"draft_sha256": _sha(draft), "ready_for_review": passed, "rounds": rounds}, indent=1),
        encoding="utf-8")


def approve(draft, reviewer, root=ROOT, override=False, today=None):
    """A person approves: the draft moves into the project and the lockfile.
    Refused unless the gates passed for this exact text (or override=True)."""
    from authoring import lock
    gates = Path(draft).with_suffix(".gates.json")
    record = json.loads(gates.read_text(encoding="utf-8")) if gates.exists() else {}
    if not override and not (record.get("ready_for_review") and record["draft_sha256"] == _sha(draft)):
        why = ("it has no recorded gate result" if not record else
               "it changed after the gates ran" if record["draft_sha256"] != _sha(draft) else
               "it did not pass the gates")
        raise NotApproved(f"{Path(draft).name} cannot be merged: {why} ({gates.name}). "
                          "Re-run `sl draft`, or pass --override to merge it anyway.")
    # The approval is the review: record who approved it and when.
    text = Path(draft).read_text(encoding="utf-8")
    day = today or date.today().isoformat()
    text = re.sub(r"(?m)^(\s*)last_reviewed: null.*$", lambda m: f'{m[1]}last_reviewed: "{day}"', text)
    text = re.sub(r"(?m)^(\s*)approver: .*$", lambda m: f"{m[1]}approver: {reviewer}", text)
    target = root / "dbt" / "models" / "semantic" / f"{Path(draft).stem}.yml"
    target.write_text(text, encoding="utf-8")
    (root / "dbt" / MART_PROPERTIES).unlink(missing_ok=True)   # its tests are in the draft now
    lock.write(reviewer, root=root)
    return target


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _project(scratch):
    from ci.regression import project
    return project(scratch)


def _explain_failure(q, error):
    """Say why a sample question failed in words a reviewer can act on."""
    if "Entity('cutoff')" not in str(error):
        lines = [line.strip() for line in str(error).splitlines() if line.strip()]
        useful = [line for line in lines if not line.startswith(("Log File", "Artifact", "If you"))]
        return f"the draft cannot answer it ({' '.join(useful[:6])[:300]})"
    # Show what the draft does instead: the same question with no cutoff filter.
    where = [layer.where_clause(k, v) for k, v in q["where"].items() if k != "cutoff"]
    try:
        total = layer.query([q["metric"]], (), where, allow_uncut=True)["total"][q["metric"]]
        shown = f"; without one it answers {total}, not {q['expected']}" \
            if total != q["expected"] else ""
    except layer.LayerError:
        shown = ""
    return f"the draft has no cutoff entity, so it cannot be limited to one cutoff{shown}"


def _tail(text, lines=4):
    return " | ".join(line.strip() for line in text.strip().splitlines()[-lines:] if line.strip())
