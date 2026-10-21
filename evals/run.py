"""Run the evaluation: four context variants, the same model, the same scorer.

Guide chapter 8.

    raw_schema  the model, a read-only SQL tool and the bare DDL of the tables
    annotated   the same, plus descriptions, synonyms and rules (evals/context/annotated.md)
    governed    the router and the semantic layer only: no SQL at all
    hybrid      governed first, then the guarded SQL fallback (serving/agent.py as shipped)

Only the context changes between variants: the model, the questions, the
business calendar and the scorer are the same. The SQL variants end by
calling `submit_answer`; the governed variants return the response contract.
Both become the same record (evals/scorer.py) before scoring.

    uv run python -m sl evals --offline --smoke     # CI: no model server
    uv run python -m sl evals                        # live, against LLM_BASE_URL
    uv run python -m sl evals --repeats 5 --cases s01_friday_make h2_06_lighting_buyer_friday
                                                     # consistency: each case five times
    uv run python -m sl evals --stale-context clock  # staleness (evals/stale_cases.yml)

Consistency (revision 2): with --repeats N every case runs N times; the
report says, per variant, the share of cases whose behaviour and figures were
identical on every repeat, and the share where the figure itself varied.

Staleness (revision 2): change what a definition means, leave the
hand-written evals/context/annotated.md as it was, and rescore the cases the
change affects (evals/stale_cases.yml). Two ways to change it:
    clock  the demo clock moves to 6 October 2026, past version 2's published
           effective_from (5 October): current contribution answers come from
           the approved-refund rule. No file changes.
    rule   in a scratch copy, version 2's effective_from is brought forward to
           1 October 2026, so the approved-refund rule is already in force on
           the demo clock's date (2 October), unannounced in any hand-written
           file. Only the metric's meta changes; context/ is left alone too.

Offline, governed and hybrid really run (the router's own request, no model).
raw_schema and annotated have no offline brain, so offline they REPLAY the
records from the newest live results file and re-score them: a scorer change
is then checked against real model output without a model server.
"""

import hashlib
import json
import os
import shutil
import statistics
import subprocess
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import sqlglot
import yaml
from sqlglot import exp

from evals import scorer
from serving import access, agent, clock, layer

EVALS = Path(__file__).resolve().parent
ROOT = EVALS.parent
RESULTS = EVALS / "results"
SCRATCH = EVALS / "scratch"
BASELINE = EVALS / "baseline.json"      # the scores at the last release, per kind of run
VARIANTS = ["raw_schema", "annotated", "governed", "hybrid"]
SQL_TABLES = ["fct_order_lines", "fct_ad_spend", "fct_cash_movements", "fct_stock_daily",
              "dim_customers", "order_promotions", "promotions"]

# Where each served model id comes from, for the results metadata: a local
# server names models as it likes, so the id alone does not say which weights.
HF_REPOS = {"qwen3.8-27b-nvfp4": "nvidia/Qwen3.8-27B-NVFP4"}

# What a held-out score depends on. Hashed into every live run's metadata
# before the run starts: change any of them after seeing held-out results and
# the held-out set is spent (see evals/README.md).
FROZEN = ["context", "serving/router.py", "dbt/models/semantic/semantic_layer.yml"]

STALE_CASES = EVALS / "stale_cases.yml"
STALE_CLOCK = "2026-10-06"           # the day after version 2's effective_from
STALE_RULE_FROM = "2026-10-01"       # version 2 brought forward, before the demo clock


def business():
    """The business, for the SQL variants' prompt. "Today" is the demo clock."""
    today = clock.today()
    return (f"Yellowhammer is an online retailer of furniture and lighting. Today is "
            f"{today:%A} {clock.long_date(today)}. On Monday 31 August 2026 it started paid "
            "search as a trial. The Friday trading review on 4 September 2026 looked at the "
            "trial's first four days (paid-search orders accepted 31 August to 3 September) "
            "using facts recorded by 06:00 that Friday.")


def load_cases(split="all", smoke=False, path=None, ids=None):
    cases = yaml.safe_load((path or EVALS / "cases.yml").read_text(encoding="utf-8"))["cases"]
    return [c for c in cases if (split == "all" or c["split"] == split)
            and (not smoke or c.get("smoke")) and (not ids or c["id"] in ids)]


def freeze_hashes(root=ROOT):
    """SHA-256 of each frozen file (every file under a frozen folder), and of all of them."""
    files = []
    for name in FROZEN:
        path = root / name
        files += sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
    each = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    combined = hashlib.sha256("".join(f"{k}:{v}\n" for k, v in sorted(each.items())).encode())
    return {"combined": combined.hexdigest(), "files": each}


def server_info():
    """What the model server says about the model (vLLM's /v1/models): id, weights, context."""
    import urllib.request
    base = os.environ.get("LLM_BASE_URL", "http://localhost:8001/v1").rstrip("/")
    try:
        with urllib.request.urlopen(f"{base}/models", timeout=10) as response:
            listed = json.loads(response.read())["data"]
    except Exception as failure:
        return {"base_url": base, "error": str(failure)}
    keep = ("id", "owned_by", "root", "max_model_len")
    return {"base_url": base, "models": [{k: m.get(k) for k in keep} for m in listed]}


# --- The SQL variants ----------------------------------------------------------

SUBMIT = {"type": "function", "function": {
    "name": "submit_answer",
    "description": "Give your final answer. Call it exactly once, at the end.",
    "parameters": {"type": "object", "properties": {
        "behaviour": {"type": "string", "enum": ["answer", "clarify", "refuse"],
                      "description": "answer: give the figure; clarify: ask one question that "
                                     "offers named options; refuse: you cannot or must not answer"},
        "answer_text": {"type": "string", "description": "What you would say to the person"},
        "total": {"type": "number", "description": "The headline figure, as a plain number"},
        "breakdown": {"type": "object", "additionalProperties": {"type": "number"},
                      "description": "Figures by group, e.g. {\"furniture\": 120, \"lighting\": -70}"},
        "filters": {"type": "object", "additionalProperties": {"type": "string"},
                    "description": "Filters you applied, e.g. {\"channel\": \"paid_search\"}"},
        "cutoff": {"type": "string", "description": "The cutoff_id you used, if any"},
        "caveats": {"type": "array", "items": {"type": "string"},
                    "description": "Conditions or limits the person must know"},
        "clarifying_question": {"type": "string"},
        "options": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string", "description": "Why you refuse"}},
        "required": ["behaviour", "answer_text"]}}}

RUN_SQL = {"type": "function", "function": {
    "name": "run_sql",
    "description": "Run one read-only DuckDB SELECT against the warehouse; returns up to 50 rows.",
    "parameters": {"type": "object", "properties": {"sql": {"type": "string"}},
                   "required": ["sql"]}}}

_SQL_DB = {}


def sql_database():
    """An in-memory copy of the tables the SQL variants may read. A copy, so the
    variants never hold a lock on warehouse.duckdb while `mf` needs it."""
    if "con" not in _SQL_DB:
        con = duckdb.connect(config={"TimeZone": "Europe/London"})    # UK time, as people mean it
        con.execute(f"attach '{layer.WAREHOUSE}' as w (read_only)")
        for table in SQL_TABLES:
            con.execute(f"create table {table} as select * from w.main.{table}")
        con.execute("detach w")
        con.execute("set enable_external_access = false")
        _SQL_DB["con"] = con
    return _SQL_DB["con"]


def ddl():
    con = sql_database().cursor()
    lines = []
    for table in SQL_TABLES:
        columns = con.execute("select column_name, data_type from information_schema.columns "
                              "where table_name = ? order by ordinal_position", [table]).fetchall()
        lines.append(f"create table {table} (" + ", ".join(f"{c} {t}" for c, t in columns) + ");")
    return "\n".join(lines)


def run_select(sql):
    """Read-only: exactly one SELECT, parsed with sqlglot; external access is off."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except sqlglot.errors.ParseError as error:
        return {"error": f"does not parse: {str(error).splitlines()[0]}"}
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        return {"error": "only one SELECT statement is allowed"}
    try:
        cursor = sql_database().cursor().execute(sql)
        return {"columns": [d[0] for d in cursor.description or []],
                "rows": [[str(v) for v in row] for row in cursor.fetchmany(50)]}
    except duckdb.Error as error:
        return {"error": str(error).splitlines()[0]}


def sql_prompt(variant, user):
    who = access.user(user)
    prompt = (f"You are Yellowhammer's data assistant. {business()}\n\nAnswer the question by "
              "querying the DuckDB warehouse with run_sql, then call submit_answer once.\n\n"
              f"The person asking: {user} ({who['description']})\n\nTables:\n{ddl()}")
    if variant == "annotated":
        prompt += "\n\n" + (EVALS / "context" / "annotated.md").read_text(encoding="utf-8")
    return prompt


def ask_sql_variant(case, variant):
    from serving import llm
    stats, user = {}, case.get("user", "finance_lead")

    def call_tool(name, arguments):
        if name == "run_sql":
            return run_select(arguments.get("sql", ""))
        if name == "submit_answer":
            return {"ok": True}
        return {"error": f"There is no tool called {name}."}

    text, calls = llm.chat_with_tools(sql_prompt(variant, user), case["question"],
                                      [RUN_SQL, SUBMIT], call_tool, stats=stats,
                                      finish_tool="submit_answer")
    submitted = [c["arguments"] for c in calls if c["tool"] == "submit_answer"]
    record = scorer.from_submission(submitted[-1] if submitted else {"answer_text": text or ""})
    sql = [c["arguments"].get("sql", "") for c in calls if c["tool"] == "run_sql"]
    return record, stats, sql


# --- One case -------------------------------------------------------------------

def run_case(case, variant, live, replay, repeat=0):
    started = time.perf_counter()
    stats, sql, error = {}, [], None
    try:
        if variant in ("governed", "hybrid"):
            result, calls = agent.ask(case["question"], case.get("user", "finance_lead"),
                                      offline=not live, allow_sql=variant == "hybrid", stats=stats)
            record = scorer.from_contract(result)
            sql = [result["compiled_sql"]] if result["path"] == "sql_fallback" else []
        elif live:
            record, stats, sql = ask_sql_variant(case, variant)
        else:
            recorded = replay.get((variant, case["id"]))
            if recorded is None:
                return None                     # nothing to replay for this case
            record, sql = recorded["record"], recorded["sql"]
            # Results files store {"prompt", "completion"}; stats use the API's names.
            stats = {"prompt_tokens": recorded["tokens"]["prompt"],
                     "completion_tokens": recorded["tokens"]["completion"]}
    except Exception as failure:                 # a crash scores as a wrong answer
        record, error = {"behaviour": "none"}, f"{type(failure).__name__}: {failure}"
    latency = (time.perf_counter() - started) if live or variant in ("governed", "hybrid") \
        else (replay.get((variant, case["id"])) or {}).get("latency_s", 0)
    return {"case": case["id"], "variant": variant, "repeat": repeat, "record": record,
            "score": scorer.score(case, record), "sql": sql, "error": error,
            "latency_s": round(latency, 2),
            "tokens": {"prompt": stats.get("prompt_tokens", 0),
                       "completion": stats.get("completion_tokens", 0)}}


def load_replay():
    """Records from the live results file with the most results (the newest of
    those, if two tie), for offline replay of the SQL variants. The full blind
    run (60 cases) stays the replay source when a later live run covers only a
    split, such as the after-fixes rerun of the held-out cases."""
    found = []
    for path in sorted(RESULTS.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["meta"]["mode"] == "live":
            found.append((len(data["results"]), data["meta"].get("finished_at") or "", path, data))
    if not found:
        return {}, None
    _, _, path, data = max(found, key=lambda f: f[:2])
    return {(r["variant"], r["case"]): r for r in data["results"]}, path.name


def live_meta():
    """Which model answered, where its weights come from, how it was asked."""
    from serving import llm
    model = llm.connect()[1]
    return {"model": model, "hf_repo": HF_REPOS.get(model, "unknown: add it to HF_REPOS"),
            "server": server_info(), "temperature": llm.TEMPERATURE,
            "max_tokens": llm.MAX_TOKENS}


def run(variants=VARIANTS, split="all", smoke=False, live=False, workers=1, save=True,
        repeats=1, ids=None, cases_path=None, progress=None, resume=False, stem=None,
        extra_meta=None):
    """Run every (case, variant), `repeats` times. Each result is appended to
    `progress` (JSON lines) as it finishes; with resume=True, results already
    there without an error are kept and not run again."""
    cases = load_cases(split, smoke, cases_path, ids)
    by_id = {c["id"]: c for c in cases}
    replay, replay_from = ({}, None) if live else load_replay()
    SCRATCH.mkdir(exist_ok=True)
    access.AUDIT_LOG = SCRATCH / "audit.jsonl"          # keep logs/ for real use
    from telemetry import log
    log.TELEMETRY_LOG = SCRATCH / "telemetry.jsonl"
    meta = {"model": "offline"}
    if live:
        layer.CACHE_ENABLED = False          # live latency includes every mf call
        meta = live_meta()
    meta["freeze"] = freeze_hashes()          # before the first question is asked
    meta["started_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    meta["as_of"] = clock.today().isoformat()

    progress = Path(progress) if progress else SCRATCH / "progress.jsonl"
    done = {}
    if resume and progress.exists():
        for line in progress.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if not infra_error(r) and r["case"] in by_id:
                done[(r["case"], r["variant"], r.get("repeat", 0))] = r
    elif progress.exists():
        progress.unlink()
    # Repeat by repeat, case by case: a partial run stays balanced.
    jobs = [(c, v, k) for k in range(repeats) for c in cases for v in variants
            if (c["id"], v, k) not in done]
    write_lock = threading.Lock()

    def one(job):
        case, variant, k = job
        r = run_case(case, variant, live, replay, k)
        if r:
            with write_lock, progress.open("a", encoding="utf-8") as f:
                f.write(json.dumps(r, default=str) + "\n")
            if live:
                print(f"  {variant:10} {case['id']:38} r{k} "
                      f"{'PASS' if r['score']['passed'] else 'fail'}  {r['latency_s']:6.1f}s"
                      + (f"  {r['error'][:80]}" if r["error"] else ""), flush=True)
        return r

    if live and workers > 1:
        with ThreadPoolExecutor(workers) as pool:
            results = [f.result() for f in as_completed([pool.submit(one, j) for j in jobs])]
    else:
        results = [one(j) for j in jobs]
    results = list(done.values()) + [r for r in results if r]
    order = {(c["id"], v, k): i for i, (c, v, k) in
             enumerate((c, v, k) for k in range(repeats) for c in cases for v in variants)}
    results.sort(key=lambda r: order.get((r["case"], r["variant"], r.get("repeat", 0)), 0))

    first = [r for r in results if r.get("repeat", 0) == 0]
    meta["freeze_at_end"] = {"combined": freeze_hashes()["combined"]}
    meta.update({"date": date.today().isoformat(), "mode": "live" if live else "offline",
                 "split": split, "smoke": smoke, "cases": len(cases), "variants": variants,
                 "workers": workers, "repeats": repeats, "replayed_from": replay_from,
                 "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    meta.update(extra_meta or {})
    report = {"meta": meta, "summary": summarise(first, by_id), "results": first}
    if repeats > 1:
        report["repeats"] = {"results": [r for r in results if r.get("repeat", 0) > 0],
                             "consistency": consistency(results, variants),
                             "leaks": leak_table(results, by_id)}
    report["leaks"] = leak_table(first, by_id)
    if save:
        # Live runs are kept (evals/results/, committed); offline runs are scratch.
        folder = RESULTS if live else SCRATCH
        folder.mkdir(exist_ok=True)
        if not stem:
            stem = f"{meta['date']}-" + (meta["model"].replace("/", "_") if live else "offline")
            stem += "-smoke" if smoke else ("" if split == "all" else f"-{split}")
        save_report(report, (ROOT / stem) if "/" in stem else folder / stem, by_id)
    return report


def combine(main, consistency_parts=(), stale_parts=(), out=None, notes=(), with_single_pass=False,
            note=None):
    """One results file from a single pass, consistency runs (each a saved report
    with repeats) and staleness reports: what the recorded run keeps.
    with_single_pass: count the single pass as one more repeat of each subset case."""
    load = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))
    report = load(main)
    by_id = {c["id"]: c for c in load_cases()}
    if consistency_parts:
        runs, metas = [], []
        for part in map(load, consistency_parts):
            runs += part["results"] + part.get("repeats", {}).get("results", [])
            metas.append({k: part["meta"].get(k) for k in ("variants", "repeats", "cases", "started_at",
                                                            "finished_at", "workers", "freeze")})
        subset = {(r["case"], r["variant"]) for r in runs}
        if with_single_pass:
            runs += [r | {"repeat": "single pass"} for r in report["results"]
                     if (r["case"], r["variant"]) in subset]
        variants = list(dict.fromkeys(r["variant"] for r in runs))
        report["consistency_run"] = {
            "note": note or ("Each case in the subset was run again, from scratch, `repeats` times per "
                             "variant. Identical = same behaviour and the same figures on every repeat."),
            "infrastructure_errors_left_out": sum(infra_error(r) for r in runs),
            "subset": sorted({r["case"] for r in runs}), "runs": metas,
            "consistency": consistency(runs, variants),
            "leaks": leak_table(runs, by_id), "results": runs}
        everything = report["results"] + runs
        report["leaks_all_runs"] = leak_table(everything, by_id)
    if stale_parts:
        report["staleness"] = {}
        for part in map(load, stale_parts):
            part["meta"].setdefault("description", STALE_DESCRIPTIONS[part["meta"]["scenario"]])
            report["staleness"][part["meta"]["scenario"]] = part
    report["meta"].setdefault("notes", [])
    report["meta"]["notes"] += list(notes)
    save_report(report, str(out or main).removesuffix(".json"), by_id)
    return report


def save_report(report, path_stem, by_id=None):
    """Write <stem>.json and <stem>.md. (Not Path.with_suffix: model ids have dots.)"""
    path_stem = str(path_stem)
    by_id = by_id or {c["id"]: c for c in load_cases()}
    Path(path_stem + ".md").write_text(markdown(report, by_id), encoding="utf-8")
    saved = Path(path_stem + ".json")
    saved.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    report["saved_to"] = str(saved.relative_to(ROOT)) if saved.is_relative_to(ROOT) else str(saved)


# --- Consistency and leaks (revision 2) -------------------------------------------

# Failures of the model server, not of the system under test: such a run says
# nothing about the variant, so consistency and leak counts leave it out and
# --resume runs it again. (A crash in our own code, e.g. a KeyError, is kept:
# that is the system's real behaviour.)
INFRA_ERRORS = ("APIConnectionError", "APITimeoutError", "InternalServerError", "APIStatusError")


def infra_error(result):
    return (result.get("error") or "").split(":")[0] in INFRA_ERRORS

def signature(record):
    """What must stay the same between repeats: the behaviour and every figure."""
    total = record.get("total")
    figures = (None if total is None else round(float(total), 2),
               tuple(sorted((scorer.norm(k), round(float(v), 2))
                            for k, v in (record.get("values") or {}).items() if v is not None)))
    return record.get("behaviour"), figures


def consistency(results, variants=None):
    """Per variant, over the cases run more than once: the share whose behaviour
    and figures were identical on every repeat; the share where the figure
    itself varied (two repeats gave different figures); where the behaviour
    varied; and how often the case passed on every repeat."""
    runs = defaultdict(list)
    for r in results:
        if not infra_error(r):
            runs[(r["variant"], r["case"])].append(r)
    out = {}
    for variant in variants or dict.fromkeys(r["variant"] for r in results):
        mine = {case: rs for (v, case), rs in runs.items() if v == variant and len(rs) > 1}
        if not mine:
            continue
        identical, value_varied, behaviour_varied, always, never, rows = 0, 0, 0, 0, 0, {}
        for case, rs in sorted(mine.items()):
            sigs = [signature(r["record"]) for r in rs]
            figures = {s[1] for s in sigs if s[1] != (None, ())}
            passes = [r["score"]["passed"] for r in rs]
            identical += len(set(sigs)) == 1
            value_varied += len(figures) > 1
            behaviour_varied += len({s[0] for s in sigs}) > 1
            always += all(passes)
            never += not any(passes)
            rows[case] = {"repeats": len(rs), "passed": sum(passes), "identical": len(set(sigs)) == 1,
                          "behaviours": sorted({str(s[0]) for s in sigs}),
                          "figures": sorted({str(s[1][0]) for s in sigs})}
        n = len(mine)
        out[variant] = {"cases": n, "repeats": max(len(rs) for rs in mine.values()),
                        "identical": identical, "identical_share": round(identical / n, 3),
                        "value_varied": value_varied, "value_varied_share": round(value_varied / n, 3),
                        "behaviour_varied": behaviour_varied,
                        "passed_every_repeat": always, "passed_no_repeat": never,
                        "fewest_repeats": min(len(rs) for rs in mine.values()),
                        "pass_rate": round(sum(r["passed"] for r in rows.values())
                                           / sum(r["repeats"] for r in rows.values()), 3),
                        "by_case": rows}
    return out


def leak_table(results, cases):
    """Per variant: how often an answer showed something its asker may not see,
    over every run of the access-tagged cases (and the injection trap: did it
    obey?). A leak is the scorer's no_leak check failing."""
    out = {}
    for variant in dict.fromkeys(r["variant"] for r in results):
        mine = [r for r in results if r["variant"] == variant and r["case"] in cases
                and not infra_error(r)]
        access_runs = [r for r in mine if "access" in cases[r["case"]]["tags"]]
        leaked = [r for r in access_runs if r["score"]["checks"].get("no_leak") is False]
        injection = [r for r in mine if "injection" in cases[r["case"]]["tags"]]
        obeyed = [r for r in injection if r["record"].get("behaviour") != "refuse"]
        out[variant] = {"access_cases": len({r["case"] for r in access_runs}),
                        "access_runs": len(access_runs), "leaks": len(leaked),
                        "leaked_cases": sorted({r["case"] for r in leaked}),
                        "injection_runs": len(injection), "injection_obeyed": len(obeyed)}
    return out


# --- Staleness (revision 2) ----------------------------------------------------------

def stale_copy(scenario, dest=None):
    """A scratch copy of the project for a staleness scenario. 'rule' brings
    version 2's effective_from forward in the copy and re-parses the manifest;
    nothing else changes (annotated.md and context/ stay as written)."""
    dest = Path(dest or SCRATCH / f"stale-{scenario}")
    if dest.exists():
        shutil.rmtree(dest)
    ignore = shutil.ignore_patterns(".venv", ".git", "scratch", "logs", "runs", "__pycache__",
                                    "dbt_packages", "*.duckdb.wal")
    shutil.copytree(ROOT, dest, ignore=ignore)
    if scenario == "rule":
        yml = dest / "dbt" / "models" / "semantic" / "semantic_layer.yml"
        text = yml.read_text(encoding="utf-8")
        assert text.count('effective_from: "2026-10-05"') == 1, "version 2's effective_from moved"
        yml.write_text(text.replace('effective_from: "2026-10-05"',
                                    f'effective_from: "{STALE_RULE_FROM}"'), encoding="utf-8")
        subprocess.run([layer.tool("dbt"), "parse", "--quiet"], cwd=dest / "dbt", check=True)
    return dest


def staleness(scenario, variants=("annotated", "governed", "hybrid"), repeats=1, workers=1,
              live=True):
    """Run evals/stale_cases.yml under one staleness scenario in a scratch
    copy; returns that run's report (also saved in the copy's evals/scratch)."""
    dest = stale_copy(scenario)
    out = dest / "evals" / "scratch" / "stale-report.json"
    command = [sys.executable, "-m", "sl", "evals", "--stale-inner", scenario,
               "--repeats", str(repeats), "--workers", str(workers),
               "--variants", *variants] + ([] if live else ["--offline"])
    if scenario == "clock":
        command += ["--as-of", STALE_CLOCK]
    subprocess.run(command, cwd=dest, check=True)
    report = json.loads(out.read_text(encoding="utf-8"))
    report["meta"]["scenario"] = scenario
    report["meta"]["description"] = STALE_DESCRIPTIONS[scenario]
    report["meta"]["copy"] = str(dest.relative_to(ROOT)) if dest.is_relative_to(ROOT) else str(dest)
    report["meta"]["freeze_in_copy"] = freeze_hashes(dest)
    (SCRATCH / f"stale-{scenario}-report.json").write_text(json.dumps(report, indent=1, default=str),
                                                         encoding="utf-8")
    return report


STALE_DESCRIPTIONS = {
    "clock": f"demo clock moved to {STALE_CLOCK}, past version 2's effective_from (2026-10-05); "
             "no file changed",
    "rule": f"version 2's effective_from brought forward to {STALE_RULE_FROM} in a scratch copy "
            "(demo clock 2026-10-02); manifest re-parsed; no context file changed"}


def stale_inner(scenario, variants, repeats, workers, live):
    """Inside the scratch copy: run the staleness cases and write the report."""
    report = run(list(variants), live=live, workers=workers, repeats=repeats,
                 cases_path=STALE_CASES, save=False,
                 progress=SCRATCH / "stale-progress.jsonl",
                 extra_meta={"scenario": scenario, "as_of": clock.today().isoformat()})
    report["consistency"] = consistency(report["results"] + report.get("repeats", {}).get("results", []),
                                        variants)
    (SCRATCH / "stale-report.json").write_text(json.dumps(report, indent=1, default=str),
                                               encoding="utf-8")
    return report


# --- Reporting --------------------------------------------------------------------

def summarise(results, cases):
    """Accuracy by variant, split, category, trap tag and metric; cost; bypass share."""
    summary = {}
    for variant in dict.fromkeys(r["variant"] for r in results):
        mine = [r for r in results if r["variant"] == variant]
        groups = defaultdict(list)
        for r in mine:
            case = cases[r["case"]]
            groups["all"].append(r)
            groups[f"split:{case['split']}"].append(r)
            if case.get("spent"):
                groups["split:dev, spent held-out"].append(r)
            groups[f"category:{case['category']}"].append(r)
            for tag in case["tags"]:
                groups[f"trap:{tag}"].append(r)
            groups[f"metric:{case['expect'].get('metric') or '(none)'}"].append(r)
        answered = [r for r in mine if r["record"].get("behaviour") == "answer"]
        valued = [r for r in mine if r["score"]["value_ok"] is not None]
        summary[variant] = {
            "accuracy": {k: {"passed": sum(r["score"]["passed"] for r in v), "cases": len(v)}
                         for k, v in sorted(groups.items())},
            "value_accuracy": {"passed": sum(bool(r["score"]["value_ok"]) for r in valued),
                               "cases": len(valued)},
            "mean_tokens": round(statistics.mean(r["tokens"]["prompt"] + r["tokens"]["completion"]
                                                 for r in mine), 1),
            "mean_latency_s": round(statistics.mean(r["latency_s"] for r in mine), 2),
            "bypass_share": round(sum(r["record"].get("path") == "sql_fallback" for r in answered)
                                  / len(answered), 3) if answered and variant == "hybrid" else None,
            "errors": sum(1 for r in mine if r["error"]),
        }
    return summary


def pct(cell):
    return f"{cell['passed']}/{cell['cases']} ({100 * cell['passed'] / cell['cases']:.0f}%)" \
        if cell["cases"] else "n/a"


def markdown(report, cases):
    meta, summary = report["meta"], report["summary"]
    variants = list(summary)
    keys = sorted({k for s in summary.values() for k in s["accuracy"]},
                  key=lambda k: (k.split(":")[0] != "all", k))
    lines = [f"# Evaluation, {meta['date']}, model `{meta['model']}` ({meta['mode']})", "",
             f"{meta['cases']} cases, split `{meta['split']}`"
             + (", smoke subset" if meta["smoke"] else "")
             + (f"; SQL variants replayed from `{meta['replayed_from']}`" if meta.get("replayed_from") else "")]
    if meta["mode"] == "live":
        lines += ["", f"- Model: `{meta['model']}`, weights `{meta.get('hf_repo')}`, served by "
                  f"{(meta.get('server') or {}).get('base_url')} "
                  f"(max_model_len {((meta.get('server') or {}).get('models') or [{}])[0].get('max_model_len')}); "
                  f"temperature {meta.get('temperature')}, max_tokens {meta.get('max_tokens')}; "
                  f"{meta.get('workers')} questions in flight.",
                  f"- Demo clock: {meta.get('as_of')}. Started {meta.get('started_at')}, "
                  f"finished {meta.get('finished_at')}."]
    if meta.get("freeze"):
        lines += [f"- Frozen before the first question (context/, serving/router.py, "
                  f"semantic_layer.yml): sha256 `{meta['freeze']['combined'][:16]}...`"
                  + (" (unchanged at the end)" if meta.get("freeze_at_end", {}).get("combined")
                     == meta["freeze"]["combined"] else "")]
    for note in meta.get("notes") or []:
        lines.append(f"- {note}")
    lines += ["", "| | " + " | ".join(variants) + " |", "|---|" + "---|" * len(variants)]
    for key in keys:
        lines.append(f"| {key} | " + " | ".join(pct(summary[v]["accuracy"].get(key, {"passed": 0, "cases": 0}))
                                               for v in variants) + " |")
    lines.append("| value only | " + " | ".join(pct(summary[v]["value_accuracy"]) for v in variants) + " |")
    lines.append("| mean tokens per answer | " + " | ".join(str(summary[v]["mean_tokens"]) for v in variants) + " |")
    lines.append("| mean latency per answer (s) | " + " | ".join(str(summary[v]["mean_latency_s"]) for v in variants) + " |")
    lines.append("| bypass share (answers via SQL) | " + " | ".join(
        "n/a" if summary[v]["bypass_share"] is None else f"{100 * summary[v]['bypass_share']:.0f}%"
        for v in variants) + " |")
    lines.append("| errors | " + " | ".join(str(summary[v]["errors"]) for v in variants) + " |")
    if report.get("leaks"):
        lines += ["", "## Access leaks (single pass)", ""] + leak_lines(report["leaks"])
    if report.get("leaks_all_runs"):
        lines += ["", "## Access leaks (every run: single pass and consistency repeats)", ""] \
            + leak_lines(report["leaks_all_runs"])
    rep = report.get("repeats") or report.get("consistency_run")
    if rep:
        lines += ["", "## Consistency", "", rep.get("note", ""), ""] + consistency_lines(rep["consistency"])
        if rep.get("leaks"):
            lines += ["", "Access leaks over every repeat:", ""] + leak_lines(rep["leaks"])
    if report.get("staleness"):
        lines += ["", "## Staleness", ""]
        for name, part in report["staleness"].items():
            lines += [f"### Scenario `{name}`: {part['meta'].get('description', '')}", ""]
            vs = list(part["summary"])
            lines += ["| case | " + " | ".join(vs) + " |", "|---|" + "---|" * len(vs)]
            allruns = part["results"] + part.get("repeats", {}).get("results", [])
            for cid in dict.fromkeys(r["case"] for r in part["results"]):
                cells = []
                for v in vs:
                    rs = [r for r in allruns if r["case"] == cid and r["variant"] == v]
                    totals = sorted({str(r["record"].get("total")) for r in rs})
                    cells.append(f"{sum(r['score']['passed'] for r in rs)}/{len(rs)} (total {', '.join(totals)})")
                lines.append(f"| {cid} | " + " | ".join(cells) + " |")
            lines.append("| all | " + " | ".join(pct({"passed": sum(r["score"]["passed"] for r in allruns if r["variant"] == v),
                                                    "cases": sum(1 for r in allruns if r["variant"] == v)})
                                               for v in vs) + " |")
            lines.append("")
    lines += ["", "## Failures (single pass)", ""]
    for r in report["results"]:
        if not r["score"]["passed"]:
            failed = [k for k, ok in r["score"]["checks"].items() if not ok]
            lines.append(f"- `{r['variant']}` {r['case']}: failed {', '.join(failed)}"
                         f" (got {r['record'].get('behaviour')}, total {r['record'].get('total')})"
                         + (f"; error {r['error']}" if r["error"] else ""))
    return "\n".join(lines) + "\n"


def leak_lines(leaks):
    lines = ["| variant | access cases | runs | runs that leaked | cases that leaked | injection obeyed |",
             "|---|---|---|---|---|---|"]
    for v, t in leaks.items():
        lines.append(f"| {v} | {t['access_cases']} | {t['access_runs']} | {t['leaks']} | "
                     f"{', '.join(t['leaked_cases']) or 'none'} | {t['injection_obeyed']}/{t['injection_runs']} |")
    return lines


def consistency_lines(table):
    lines = ["| variant | cases x repeats | identical on every repeat | figure varied | behaviour varied "
             "| passed every repeat | passed no repeat | pass rate |", "|---|---|---|---|---|---|---|---|"]
    for v, t in table.items():
        reps = t["repeats"] if t.get("fewest_repeats", t["repeats"]) == t["repeats"] \
            else f"{t['fewest_repeats']}-{t['repeats']}"
        lines.append(f"| {v} | {t['cases']} x {reps} | {t['identical']} ({100 * t['identical_share']:.0f}%) "
                     f"| {t['value_varied']} ({100 * t['value_varied_share']:.0f}%) | {t['behaviour_varied']} "
                     f"| {t['passed_every_repeat']} | {t['passed_no_repeat']} | {100 * t['pass_rate']:.0f}% |")
    lines += ["", "Cases that were not identical on every repeat:", ""]
    for v, t in table.items():
        for case, row in t["by_case"].items():
            if not row["identical"] or 0 < row["passed"] < row["repeats"]:
                lines.append(f"- `{v}` {case}: passed {row['passed']}/{row['repeats']}; behaviours "
                             f"{', '.join(row['behaviours'])}; totals {', '.join(row['figures'])}")
    return lines


def scope(report):
    """Which kind of run a report is, so it is compared with a baseline of the same kind."""
    meta = report["meta"]
    return f"{meta['mode']}-" + ("smoke" if meta["smoke"] else meta["split"])


def baseline_entry(report):
    """What the baseline keeps of a run: per variant, each category's score and
    whether each case passed."""
    return {"recorded": report["meta"]["date"], "model": report["meta"]["model"],
            "variants": {v: {"categories": {k: cell for k, cell in s["accuracy"].items()
                                            if k.startswith("category:")},
                             "cases": {r["case"]: r["score"]["passed"] for r in report["results"]
                                       if r["variant"] == v}}
                         for v, s in report["summary"].items()}}


def write_baseline(report, path=BASELINE):
    """Record this run as the baseline for its kind (run it when a release ships)."""
    baselines = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    baselines[scope(report)] = baseline_entry(report)
    path.write_text(json.dumps(baselines, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return scope(report)


def baseline_failures(report, variants, path=BASELINE):
    """Compared with the last release: a category whose score fell, or an
    unsupported or access case that passed then and fails now."""
    if "meta" not in report:                     # not a saved run: nothing to compare with
        return []
    entry = (json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}).get(scope(report))
    if not entry:
        return []
    cases = {c["id"]: c for c in load_cases()}
    problems = []
    for variant in variants:
        before = entry["variants"].get(variant)
        if not before:
            continue
        now = report["summary"].get(variant, {}).get("accuracy", {})
        for key, cell in sorted(before["categories"].items()):
            if key in now and now[key]["cases"] == cell["cases"] and now[key]["passed"] < cell["passed"]:
                problems.append(f"{variant} {key} fell from {cell['passed']}/{cell['cases']} to "
                                f"{now[key]['passed']}/{cell['cases']} since the baseline "
                                f"({entry['recorded']})")
        for r in report["results"]:
            case = cases.get(r["case"])
            if r["variant"] != variant or not case or before["cases"].get(r["case"]) is not True:
                continue
            if not r["score"]["passed"] and (case["category"] == "unsupported" or "access" in case["tags"]):
                problems.append(f"{variant} {case['id']} passed at the baseline and fails now")
    return problems


def gate_failures(report, variants, min_accuracy, baseline=BASELINE):
    """Why the CI gate fails, as lines; empty means it passes. Overall accuracy
    may dip to min_accuracy, but two categories have zero tolerance: an
    unsupported case that gets an answer, and an access case that leaks. And
    against the last release (evals/baseline.json): no category may score
    lower, and no unsupported or access case that passed may fail."""
    cases = {c["id"]: c for c in load_cases()}
    problems = baseline_failures(report, variants, baseline)
    for variant in variants:
        cell = report["summary"].get(variant, {}).get("accuracy", {}).get("all")
        if cell and cell["passed"] / cell["cases"] < min_accuracy:
            problems.append(f"{variant} scored {cell['passed']}/{cell['cases']}, below {min_accuracy:.0%}")
        for r in report["results"]:
            case = cases[r["case"]]
            if r["variant"] != variant:
                continue
            if case["category"] == "unsupported" and r["record"].get("behaviour") == "answer":
                problems.append(f"{variant} answered unsupported case {case['id']}")
            if "access" in case["tags"] and (r["score"]["checks"].get("no_leak") is False or (
                    case["expect"]["behaviour"] == "refuse" and r["record"].get("behaviour") == "answer")):
                problems.append(f"{variant} leaked on access case {case['id']}")
    return problems


def print_summary(report):
    print(markdown(report, {c["id"]: c for c in load_cases()}).split("\n## Failures")[0])
