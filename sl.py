"""sl: one command for every running-example step.

    uv run python -m sl <command>        (any OS)
    ./sl <command>                       (Linux and macOS shortcut)

Run with no arguments for the list of commands.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DBT_DIR = ROOT / "dbt"
BIN = Path(sys.executable).parent
USERS = ["finance_lead", "commercial_lead", "lighting_buyer"]


# --- Chapter 2: build and test the warehouse ---------------------------------

# --full-refresh: the seeds are small, and a refreshed seed never keeps an old
# column type (a timestamp column becoming TIMESTAMPTZ, say) in the warehouse.
BUILD = ("build", "--full-refresh")


def build(_args):
    """Load the seeds, build the models and run every dbt test."""
    return dbt(*BUILD)


def test(_args):
    """dbt build (with its tests), then the Python tests. No model server needed."""
    code = dbt(*BUILD)
    return code or subprocess.call([sys.executable, "-m", "pytest", "-q"], cwd=ROOT)


# The two breakages, kept here so the CLI and the tests break the same thing.
FAN_OUT_BREAK = ("    on promotions.order_id = lines.order_id",
                 "    on promotions.order_id = lines.order_id\n"
                 "left join {{ ref('stg_order_promotions') }} as naive\n"
                 "    on naive.order_id = lines.order_id")
LAMP_BREAK = ("sum(case when paid_at is not null then amount else 0 end)",
              "sum(case when approved_at is not null then amount else 0 end)")


def prove_fan_out(_args):
    """Join the many-to-many promotions table naively in a scratch copy; watch the tests fail."""
    print("In a scratch copy of dbt/, fct_order_lines joins order_promotions naively,\n"
          "so each line repeats once per promotion. Building it:\n", flush=True)
    code, _ = break_and_build(*FAN_OUT_BREAK)
    print("\nExpected: unique_fct_order_lines_snapshot_key and "
          "goods_value_reconciles_with_order_lines FAIL. The real project was not touched.")
    return 0 if code != 0 else 1


def prove_lamp_test(_args):
    """Code the approved-refund reading into version 1 in a scratch copy; watch the lamp test fail."""
    print("In a scratch copy of dbt/, version 1 deducts approved refunds, paid or not.\n"
          "Building it:\n", flush=True)
    code, _ = break_and_build(*LAMP_BREAK)
    print("\nExpected: lamp_line_paid_refund_rule_gives_0 FAILS (expected 0, got -50). "
          "The real project was not touched.")
    return 0 if code != 0 else 1


def break_and_build(old, new, capture=False):
    """Copy dbt/ to a temporary folder (with its own warehouse), replace one piece of
    fct_order_lines.sql, and build it. Returns (exit code, output if captured)."""
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp) / "dbt"
        shutil.copytree(DBT_DIR, scratch, ignore=shutil.ignore_patterns("target", "logs"))
        model = scratch / "models" / "marts" / "fct_order_lines.sql"
        sql = model.read_text(encoding="utf-8")
        assert old in sql, "could not find the code to break"
        model.write_text(sql.replace(old, new, 1), encoding="utf-8")
        result = dbt("build", project=scratch, capture=capture)
        return result if capture else (result, "")


def dbt(*arguments, project=None, capture=False):
    project = project or DBT_DIR
    exe = shutil.which("dbt", path=str(BIN)) or "dbt"
    command = [exe, *arguments, "--project-dir", str(project), "--profiles-dir", str(project)]
    if not capture:
        return subprocess.call(command, cwd=project)
    done = subprocess.run(command, cwd=project, capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "PYTHONUTF8": "1"})
    return done.returncode, done.stdout + done.stderr


# --- Chapter 3 and 4: the layer, the router, the contract ---------------------

def metrics(args):
    from serving import tools
    for m in tools.list_metrics(args.user):
        print(f"{m['name']:28} {m['status']:9} v{m['version']}  {m['owner']:16} {m['label']}")
    return 0


def dims(args):
    from serving import tools
    print(json.dumps(tools.get_dimensions(args.user, args.metric), indent=2))
    return 0


def explain(args):
    """Print the mf command and the SQL MetricFlow compiles for a request."""
    from serving import layer, tools
    where = [layer.where_clause("cutoff", args.cutoff or tools.DEFAULT_CUTOFF)]
    where += [layer.where_clause(*f.split("=", 1)) for f in args.filter]
    arguments = layer.mf_arguments([args.metric], args.group_by, where, args.start, args.end)
    print(layer.printable("mf", *arguments, "--explain"), "\n")
    print(layer._sql_from_explain(layer.run_mf(*arguments, "--explain")))
    return 0


def query(args):
    from serving import tools
    filters = dict(f.split("=", 1) for f in args.filter)
    show(tools.query_metrics(args.user, args.metric, args.group_by, filters,
                             args.start, args.end, args.cutoff), as_json=args.json)
    return 0


def sql(args):
    from serving import tools
    show(tools.run_readonly_sql(args.user, args.sql, args.cutoff), as_json=args.json)
    return 0


def route(args):
    from serving import router
    print(json.dumps(router.classify(args.question).__dict__, indent=2))
    return 0


def ask(args):
    from serving import agent, clock
    users = USERS if args.user == "all" else [args.user]
    print(clock.describe_clock() + "\n")
    for user in users:
        if not args.offline:
            from serving import llm
            print(f"(Asking {llm.describe()} as {user})", flush=True)
        result, calls = agent.ask(args.question, user, offline=args.offline)
        show(result, as_json=args.json)
        if args.show_tools:
            for call in calls:
                print(f"  tool: {call['tool']}({json.dumps(call['arguments'])})")
        print()
    return 0


def compare(args):
    """The book's argument: the governed assistant and a raw-SQL wrapper, same model."""
    from serving import agent, llm, wrapper
    from serving import clock
    print(f"{clock.describe_clock()}. (Both use {llm.describe()})\n")
    print("=" * 72 + "\nGOVERNED: router, semantic layer, response contract\n" + "=" * 72)
    show(agent.ask(args.question, args.user)[0])
    print("\n" + "=" * 72 + "\nWRAPPER: raw tables and its own SQL\n" + "=" * 72)
    wrapper.run(args.question, runs=args.runs)
    return 0


def wrapper_cmd(args):
    from serving import wrapper
    wrapper.run(args.question, runs=args.runs)
    return 0


def mcp(args):
    from serving import mcp_server
    mcp_server.main(["--user", args.user] if args.user else [])   # else SL_USER, or refuse
    return 0


def audit(args):
    from serving.access import AUDIT_LOG
    lines = AUDIT_LOG.read_text(encoding="utf-8").splitlines() if AUDIT_LOG.exists() else []
    for line in lines[-args.tail:]:
        r = json.loads(line)
        print(f"{r['at']}  {r.get('user', ''):15} {r.get('tool', 'ask'):16} "
              f"{r.get('path', ''):12} {r.get('metric', '') or ''} "
              f"{'v' + str(r['version']) if r.get('version') else ''}  {r.get('question', '')[:50]}")
    return 0


# --- Chapter 8: evaluations ---------------------------------------------------

def evals(args):
    """Score the test set for each context variant (offline: no model server)."""
    from evals import run
    variants = args.variants or run.VARIANTS     # offline, the SQL variants replay the last live run
    if not args.offline:
        from serving import llm
        print(f"(Live: {llm.describe()})", flush=True)
    if args.stale_inner:                         # inside a staleness scratch copy
        run.stale_inner(args.stale_inner, variants, args.repeats, args.workers, not args.offline)
        return 0
    if args.stale_context:
        variants = args.variants or ["annotated", "governed", "hybrid"]
        report = run.staleness(args.stale_context, variants, args.repeats, args.workers,
                               live=not args.offline)
        print("\n".join(run.markdown({**report, "staleness": {args.stale_context: report}},
                                      {c["id"]: c for c in run.load_cases(path=run.STALE_CASES)})
                         .split("\n## Failures")[0].split("\n## Staleness\n")[1:]))
        return 0
    report = run.run(variants, args.split, args.smoke, live=not args.offline,
                     workers=args.workers, repeats=args.repeats, ids=args.cases,
                     progress=args.progress, resume=args.resume, stem=args.save_as)
    run.print_summary(report)
    if report.get("saved_to"):
        print(f"Saved to {report['saved_to']} (and .md)")
    if args.write_baseline:                      # at a release: what later runs must keep
        print(f"Baseline for {run.write_baseline(report)} written to evals/baseline.json")
    if args.min_accuracy is not None:            # the CI gate
        problems = run.gate_failures(report, args.gate or ["governed", "hybrid"], args.min_accuracy)
        for problem in problems:
            print(f"FAIL: {problem}")
        return 1 if problems else 0
    return 0


# --- Chapter 9: change management ---------------------------------------------

def diff(args):
    """The breaking-change gate: this project's manifest against production's."""
    from change import demo, manifest_diff
    head = manifest_diff.load(DBT_DIR / "target" / "semantic_manifest.json")
    allow = args.allow_breaking or os.environ.get("ALLOW_BREAKING_CHANGE") == "1"
    print(f"Semantic manifest diff against {args.base or 'change/production_manifest.json'}:")
    code, lines = demo.gate(demo.base_manifest(args.base), head, allow_breaking=allow)
    print("\n".join(lines))
    return code


def change_demo(args):
    from change import demo
    return demo.change_demo(args)


def release(args):
    from change import demo
    return demo.release(args)


# --- Chapter 5: AI-drafted models -----------------------------------------------

def draft(args):
    """An agent drafts a semantic model for fct_refunds; the gates decide."""
    from authoring import draft as drafting
    mode = "offline: canned drafts" if args.offline else "live model"
    print(f"Drafting a semantic model for {drafting.MART} ({mode}); at most "
          f"{drafting.MAX_ROUNDS} rounds.\n", flush=True)
    result = drafting.run(live=not args.offline)
    for r in result["rounds"]:
        print(f"Round {r['round']}:")
        for g in r["gates"]:
            print(f"  {'PASS' if g['passed'] else 'FAIL'} {g['gate']:10} {g['detail']}")
    where = result["draft"].relative_to(ROOT)
    print(f"\nDraft written to {where} (not merged).")
    if result["ready_for_review"]:
        print("Human approval required. A reviewer reads the draft, then runs:\n"
              f"  uv run python -m sl approve-draft {where} --reviewer \"finance lead\"")
        return 0
    print("The draft did not pass the gates in two rounds: a person must decide what to do.")
    return 1


def approve_draft(args):
    from authoring import draft as drafting
    try:
        target = drafting.approve(Path(args.path), args.reviewer, override=args.override)
    except drafting.NotApproved as refused:
        print(f"Not merged: {refused}")
        return 1
    print(f"Merged into {target.relative_to(ROOT)}; lockfile updated, reviewed by {args.reviewer}.")
    print("Run `uv run python -m sl build` and the tests next.")
    return 0


def lock(args):
    from authoring import lock as locking
    files = locking.write(args.reviewer)
    print(f"Locked {len(files)} reviewed files in {locking.LOCKFILE}.")
    return 0


def check_lock(_args):
    from authoring import lock as locking
    problems = locking.check()
    for problem in problems:
        print(f"  UNREVIEWED {problem}")
    print("check-lock: FAIL. A reviewer must review these files, then run `sl lock`."
          if problems else "check-lock: every reviewed file matches its review.")
    return 1 if problems else 0


# --- Chapter 10: telemetry -----------------------------------------------------

def telemetry_demo(args):
    from telemetry import demo
    return demo.main(args)


def telemetry_report(args):
    from telemetry import log, report
    records = log.read(args.path)
    if not records:
        print(f"No telemetry in {args.path or log.TELEMETRY_LOG}. Ask some questions first.")
        return 0
    print(report.render(report.analyse(records), f"Telemetry report: {len(records)} answers"))
    return 0


# --- Chapter 6: CI ------------------------------------------------------------

def ranges(_args):
    """ch 10: store each metric's usual daily range (serving/expected_ranges.json)."""
    from serving import unusual
    for name, r in unusual.write().items():
        print(f"{name:24} {r['kind']:8} {r['low']:>12,.2f} to {r['high']:>10,.2f}  ({r['days']} days)")
    print(f"Written to {unusual.RANGES.relative_to(ROOT)}. Answers outside these ranges carry a caveat.")
    return 0


def regress(args):
    from ci import regression
    return regression.main(args)


# Warnings `sl validate` accepts, each with its reason. Any other warning fails.
ALLOWED_WARNINGS = {
    "Metric 'goods_value_mtd' was included in the OSI document":
        "dbt 1.12 also writes target/osi_document.json (Open Semantic Interchange), which "
        "cannot hold a cumulative metric's grain_to_date; MetricFlow's own answers are unaffected",
}


def validate(_args, project=DBT_DIR):
    """dbt parse first, so the manifest is today's YAML, then mf validate-configs
    with the checks against the warehouse. Fails on any error, and on any
    warning (dbt's or MetricFlow's) that is not in ALLOWED_WARNINGS."""
    import re
    code, parsed = dbt("parse", project=project, capture=True)
    if code:
        print(parsed[-2000:] + "\ndbt parse failed.")
        return 1
    exe = shutil.which("mf", path=str(BIN)) or "mf"
    done = subprocess.run([exe, "validate-configs", "--show-all"], cwd=project, capture_output=True,
                          text=True, encoding="utf-8", env={**os.environ, "PYTHONUTF8": "1"})
    output = done.stdout + done.stderr
    parsed = re.sub(r"\x1b\[[0-9;]*m", "", parsed)     # dbt colours its log on a terminal
    for line in output.replace("\r", "\n").splitlines():
        if "validated" in line or ("ERROR" in line and "ERRORS: 0" not in line):
            print(line.strip())
    warnings = [line.split("WARNING", 1)[1].strip(" :]") for line in
                (parsed + output).replace("\r", "\n").splitlines()
                if "[WARNING]" in line or line.startswith("• WARNING")]
    unexpected = 0
    for warning in warnings:
        reason = next((why for text, why in ALLOWED_WARNINGS.items() if text in warning), None)
        print(f"WARNING{' (allowed: ' + reason + ')' if reason else ''}: {warning}")
        unexpected += reason is None
    if unexpected:
        print(f"{unexpected} warning(s) not in ALLOWED_WARNINGS (sl.py): fix them or allow them, with a reason.")
    return 1 if done.returncode or re.search(r"ERRORS: [1-9]", output) or unexpected else 0


# The CI pipeline (guide ch 6). .github/workflows/ci.yml runs the same steps,
# in the same order; tests/test_ci.py checks the two lists agree.
CI_STEPS = [
    ("build", ["build"]),                     # seeds, models, data tests, unit tests
    ("validate", ["validate"]),               # mf validate-configs
    ("pytest", ["pytest"]),                   # Python tests, metric regression included
    ("evals-smoke", ["evals", "--offline", "--smoke", "--min-accuracy", "0.85"]),
    ("check-lock", ["check-lock"]),           # no unreviewed edit to a reviewed file
    ("diff", ["diff"]),                       # breaking-change gate against the base
]


def pytest_cmd(args):
    return subprocess.call([sys.executable, "-m", "pytest", "-q", *args.pytest_args], cwd=ROOT)


def ci(args):
    """Run every CI step locally, as GitHub Actions would, and summarise."""
    results = []
    for name, step in CI_STEPS:
        if name == "diff" and args.base:
            step = step + ["--base", args.base]
        print(f"\n{'=' * 72}\nCI step: {name}  (python -m sl {' '.join(step)})\n{'=' * 72}", flush=True)
        started = time.perf_counter()
        code = subprocess.call([sys.executable, "-m", "sl", *step], cwd=ROOT)
        results.append((name, code, time.perf_counter() - started))
    print(f"\n{'=' * 72}\nCI summary\n{'=' * 72}")
    for name, code, seconds in results:
        print(f"  {'PASS' if code == 0 else 'FAIL'}  {name:12} {seconds:6.1f}s")
    return 0 if all(code == 0 for _, code, _ in results) else 1


def show(c, as_json=False):
    """Print a response contract for a person (or as JSON)."""
    if as_json:
        print(json.dumps(c, indent=2, default=str))
        return
    print(f"[{c['user']}] {c['question']}" if c["question"] else f"[{c['user']}]")
    print(f"  Category: {c['category']}   Path: {c['path']}")
    print(f"  Answer: {c['answer_text']}")
    if c["clarification"]:
        print(f"  Options: {', '.join(c['clarification']['options'])}")
    for row in c["values"][:15]:
        print(f"    {row}")
    if c["filters_applied"]:
        print("  Filters: " + "; ".join(f"{f['field']} = {f['value']} ({f['source']})"
                                        for f in c["filters_applied"]))
    if c["time_window"]:
        # The window is on the metric's time dimension: order dates, or for
        # stock the dates of the snapshots (the last one in the window counts).
        from serving import layer
        stock = c["metrics"] and layer.agg_time_dimensions(c["metrics"][0]) == {"snapshot_date"}
        print(f"  {'Snapshot dates' if stock else 'Orders accepted'}: "
              f"{c['time_window']['start']} to {c['time_window']['end']}")
    if c["cutoff"]:
        from serving import clock
        print(f"  Cutoff: {c['cutoff']['id']} (facts recorded by {clock.uk_time(c['cutoff']['at'])})")
    for line in c["lineage"]:
        print(f"  Lineage: {line['metric']} v{line['version']}, {line['status']}, "
              f"owner {line['owner']}, approver {line['approver']}, reviewed {line['last_reviewed']}")
    for caveat in c["caveats"]:
        print(f"  Caveat: {caveat}")
    if c["compiled_sql"]:
        print("  SQL: " + " ".join(c["compiled_sql"].split())[:300] + " ...")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="sl", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name, handler, help):
        p = sub.add_parser(name, help=help)
        p.set_defaults(handler=handler)
        return p

    def user_arg(p, allow_all=False):
        p.add_argument("--user", default="finance_lead",
                       choices=USERS + (["all"] if allow_all else []))

    def as_of_arg(p):
        p.add_argument("--as-of", help="move the demo clock (default 2026-10-02), e.g. 2026-10-06")

    def request_args(p):
        p.add_argument("metric")
        p.add_argument("--group-by", nargs="*", default=[])
        p.add_argument("--filter", nargs="*", default=[], help="name=value, e.g. channel=paid_search")
        p.add_argument("--start")
        p.add_argument("--end")
        p.add_argument("--cutoff", help="friday_review, lamp_recalculation or september_close")

    command("build", build, "ch 2: load the seeds, build the models, run the dbt tests")
    command("test", test, "dbt tests and Python tests (no model server needed)")
    command("prove-fan-out", prove_fan_out, "ch 2: break the promotions join in a scratch copy")
    command("prove-lamp-test", prove_lamp_test, "ch 2: break the refund rule in a scratch copy")
    p = command("metrics", metrics, "ch 4: the metrics a user may ask for")
    user_arg(p)
    p = command("dims", dims, "ch 4: dimensions for one metric")
    p.add_argument("metric")
    user_arg(p)
    p = command("explain", explain, "ch 4: the SQL MetricFlow compiles for a request")
    request_args(p)
    p = command("query", query, "ch 4: a governed query, returned as the response contract")
    request_args(p)
    user_arg(p)
    as_of_arg(p)
    p.add_argument("--json", action="store_true")
    p = command("sql", sql, "ch 4, 7: read-only SQL through the SQL guard")
    p.add_argument("sql")
    p.add_argument("--cutoff")
    user_arg(p)
    p.add_argument("--json", action="store_true")
    p = command("route", route, "ch 3, 4: how the router classifies a question")
    p.add_argument("question")
    as_of_arg(p)
    p = command("ask", ask, "ch 4, 7: ask the governed assistant (--user all: every user)")
    p.add_argument("question")
    user_arg(p, allow_all=True)
    p.add_argument("--offline", action="store_true", help="no model: follow the router's request")
    p.add_argument("--json", action="store_true", help="print the whole response contract")
    p.add_argument("--show-tools", action="store_true")
    as_of_arg(p)
    p = command("compare", compare, "book ch 1: governed assistant vs raw-SQL wrapper")
    p.add_argument("question")
    as_of_arg(p)
    p.add_argument("--runs", type=int, default=1)
    user_arg(p)
    p = command("wrapper", wrapper_cmd, "book ch 1: the raw-SQL wrapper alone, several runs")
    p.add_argument("question")
    p.add_argument("--runs", type=int, default=5)
    p = command("mcp", mcp, "ch 4: run the MCP server on stdio for one user")
    p.add_argument("--user", choices=USERS, help="required, or set SL_USER: there is no default")
    p = command("audit", audit, "ch 7: the last entries in logs/audit.jsonl")
    p.add_argument("--tail", type=int, default=20)

    p = command("evals", evals, "ch 8: score 60 questions for each context variant")
    p.add_argument("--offline", action="store_true",
                   help="no model: governed and hybrid run; SQL variants replay the last live run")
    p.add_argument("--smoke", action="store_true", help="the 8-case subset for pull requests")
    p.add_argument("--split", default="all", choices=["all", "dev", "held_out"])
    p.add_argument("--variants", nargs="*", choices=["raw_schema", "annotated", "governed", "hybrid"])
    p.add_argument("--workers", type=int, default=1, help="live: questions in flight at once")
    p.add_argument("--min-accuracy", type=float,
                   help="fail below this share, on any unsupported case answered or access "
                        "case leaked, or on a fall since evals/baseline.json (CI gate)")
    p.add_argument("--gate", nargs="*", help="variants the gate applies to (default governed, hybrid)")
    p.add_argument("--write-baseline", action="store_true",
                   help="record this run in evals/baseline.json (do it when a release ships)")
    p.add_argument("--repeats", type=int, default=1,
                   help="run each case N times and report consistency")
    p.add_argument("--cases", nargs="*", help="only these case ids")
    p.add_argument("--progress", help="append each result to this JSON-lines file")
    p.add_argument("--resume", action="store_true", help="keep results already in --progress")
    p.add_argument("--save-as", help="file name (no suffix) for the saved report")
    p.add_argument("--stale-context", choices=["clock", "rule"],
                   help="staleness: change a definition in a scratch copy, keep annotated.md")
    p.add_argument("--stale-inner", choices=["clock", "rule"], help=argparse.SUPPRESS)
    as_of_arg(p)

    p = command("diff", diff, "ch 9: classify metric changes against production; fail on breaking")
    p.add_argument("--base", help="a manifest JSON file or a git ref (default: production manifest)")
    p.add_argument("--allow-breaking", action="store_true", help="a business owner approved it")
    command("change-demo", change_demo, "ch 9: change a formula in place (CI fails), then ship v2")
    command("release", release, "ch 9: record the current manifest as what is in production")
    command("regress", regress, "ch 6: metric regression test on frozen data")
    command("ranges", ranges, "ch 10: store each metric's usual daily range, for unusual values")
    command("validate", validate, "ch 6: mf validate-configs, with warehouse checks")
    p = command("pytest", pytest_cmd, "ch 6: the Python tests only")
    p.add_argument("pytest_args", nargs="*")
    p = command("ci", ci, "ch 6: every CI step, locally, as GitHub Actions runs them")
    p.add_argument("--base", help="diff against this manifest file or git ref")
    p = command("draft", draft, "ch 5: an agent drafts a semantic model; gates, critic, person")
    p.add_argument("--offline", action="store_true", help="canned drafts: round 1 fails a gate")
    p = command("approve-draft", approve_draft, "ch 5: a person merges a draft that passed")
    p.add_argument("path")
    p.add_argument("--reviewer", required=True)
    p.add_argument("--override", action="store_true", help="merge a draft that did not pass its gates")
    p = command("lock", lock, "ch 5: record hashes of the reviewed semantic and context files")
    p.add_argument("--reviewer", required=True)
    command("check-lock", check_lock, "ch 5: fail if a reviewed file changed without review")
    command("telemetry-demo", telemetry_demo,
            "ch 10: a simulated week, one fallback cluster promoted, bypass falls")
    p = command("telemetry-report", telemetry_report, "ch 10: report on logs/telemetry.jsonl")
    p.add_argument("path", nargs="?")

    sys.stdout.reconfigure(encoding="utf-8")       # £ and − on any console
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        parser.print_help()
        return 0
    args = parser.parse_args(argv)
    if getattr(args, "as_of", None):
        from serving import clock
        clock.set_as_of(args.as_of)              # the demo clock, for this command only
    try:
        return args.handler(args)
    except Exception as error:
        if type(error).__name__ in ("APIConnectionError", "APITimeoutError"):
            sys.exit("Could not reach the model server. Start one, set LLM_BASE_URL, "
                     "or add --offline.")
        raise


if __name__ == "__main__":
    sys.exit(main())
