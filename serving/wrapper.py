"""The wrapper: the same model, given raw tables and a SQL tool (book chapter 1).

This is how many AI assistants on data are built today, and it is the
comparison for `sl compare`. It gets the business context, the schemas of the
raw tables as they stood at 06:00 on Friday 4 September 2026, and a run_sql
tool with no guard. Nobody tells it which refunds count: the rule is not
written anywhere it can reach, so it has to choose.

Each run is saved, with the SQL it wrote, under runs/.
"""

import json
from datetime import datetime

import duckdb

from serving import llm
from serving.layer import ROOT, WAREHOUSE

RUNS_DIR = ROOT / "runs"
FRIDAY_CUTOFF = "2026-09-04 06:00:00+01:00"     # 06:00 BST, with its offset
MAX_TURNS = 8

BUSINESS_CONTEXT = (
    "Yellowhammer is an online retailer of furniture and lighting. On Monday 31 August "
    "2026 it started paid search as a trial. This conversation is part of the Friday "
    "trading review on Friday 4 September 2026, and uses what Yellowhammer had recorded "
    "by 06:00 that morning (UK time). 'Last week' and 'the trial' mean the trial's first "
    "four days: orders accepted from Monday 31 August to Thursday 3 September 2026."
)

# The raw tables, cut back to what had been recorded by the Friday cutoff:
# on Friday morning nothing later existed.
RAW_TABLES = ["orders", "order_lines", "cost_estimates", "refunds", "payments", "ad_spend"]
LOAD = [
    "create table orders as select order_id, accepted_at, channel, status from w.orders "
    "where recorded_at <= '{cutoff}'",
    "create table order_lines as select * from w.order_lines "
    "where order_id in (select order_id from orders)",
    "create table cost_estimates as select * from w.cost_estimates "
    "where order_line_id in (select order_line_id from order_lines)",
    "create table refunds as select refund_id, order_line_id, event, event_at, amount, reason "
    "from w.refund_events where recorded_at <= '{cutoff}'",
    "create table payments as select * exclude (recorded_at) from w.payments "
    "where recorded_at <= '{cutoff}'",
    "create table ad_spend as select * exclude (recorded_at) from w.ad_spend "
    "where recorded_at <= '{cutoff}'",
]


def open_raw_tables():
    """An in-memory copy of the raw tables only: no models, no semantic layer."""
    # UK time for this session: times the model reads or writes ('2026-09-04')
    # mean UK time, and the stored UTC instants are shown in it, with offset.
    con = duckdb.connect(config={"TimeZone": "Europe/London"})
    con.execute(f"attach '{WAREHOUSE}' as w (read_only)")
    for statement in LOAD:
        con.execute(statement.format(cutoff=FRIDAY_CUTOFF))
    con.execute("detach w")
    con.execute("set enable_external_access = false")      # no files, no attaching
    return con


def schema_text(con):
    lines = []
    for table in RAW_TABLES:
        columns = con.execute("select column_name, data_type from information_schema.columns "
                              "where table_name = ? order by ordinal_position", [table]).fetchall()
        lines.append(f"{table}(" + ", ".join(f"{c} {t}" for c, t in columns) + ")")
    return "\n".join(lines)


def system_prompt(con):
    return (f"You are Yellowhammer's AI data assistant. You answer questions from staff by "
            f"querying the data warehouse (DuckDB) with the run_sql tool.\n\n{BUSINESS_CONTEXT}"
            f"\n\nWarehouse tables (timestamps with time zone, shown in UK time):\n"
            f"{schema_text(con)}\n\n"
            "Answer the question directly and concisely, with the figure.")


TOOLS = [{"type": "function", "function": {
    "name": "run_sql",
    "description": "Run one DuckDB SQL query against the warehouse and return the rows.",
    "parameters": {"type": "object", "properties": {"sql": {"type": "string"}},
                   "required": ["sql"]}}}]


def run_once(question):
    con = open_raw_tables()

    def call_tool(name, arguments):
        if name != "run_sql":
            return {"error": f"There is no tool called {name}."}
        try:
            cursor = con.execute(arguments.get("sql", ""))
            columns = [d[0] for d in cursor.description or []]
            return {"columns": columns, "rows": [list(map(str, r)) for r in cursor.fetchmany(50)]}
        except duckdb.Error as error:
            return {"error": str(error)}

    answer, calls = llm.chat_with_tools(system_prompt(con), question, TOOLS, call_tool,
                                        max_turns=MAX_TURNS)
    if answer is None:                      # used every turn without a final message
        answer = f"(no answer after {MAX_TURNS} turns)"
    return {"answer": answer, "sql": [c["arguments"].get("sql", "") for c in calls]}


def run(question, runs=1):
    """Ask the wrapper the same question several times and keep every run."""
    model = llm.describe()
    started = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    results = []
    for number in range(1, runs + 1):
        print(f"\n--- Wrapper run {number} of {runs} ---", flush=True)
        result = run_once(question)
        results.append(result)
        print(result["answer"])
        for sql in result["sql"]:
            print("\nSQL it wrote:\n" + sql)
    RUNS_DIR.mkdir(exist_ok=True)
    path = RUNS_DIR / f"wrapper-{started}.json"
    path.write_text(json.dumps({"question": question, "model": model, "started": started,
                                "runs": results}, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved to {path.relative_to(ROOT)}")
    return results
