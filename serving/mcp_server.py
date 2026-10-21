"""An MCP server for the Yellowhammer semantic layer (guide chapter 4).

    uv run python -m sl mcp --user finance_lead

It speaks MCP over stdio, so any MCP client (Claude Desktop, an IDE, the
`mcp` Python client in tests/test_mcp.py) can call four tools. The server is
started for ONE signed-in user, a stand-in for OAuth: the identity comes from
the session, never from a tool argument the model could change.

The question, when the client passes it, is routed first: an unsupported
question is refused before any query runs. Each answer is written to the
telemetry log, as on the agent path, refusals and blocked fallbacks included.

Say plainly what that routing is worth. An MCP server never sees the user's
message: it classifies the `question` argument the client sends, and a model
can misstate it (declare a question no governed metric fits, to get raw SQL).
The order "governed first, SQL second" is therefore steered and audited here,
not guaranteed. What does hold whatever the model writes: the identity (set
at start-up), the row filters and allow-lists in code, and above all the
warehouse role the fallback runs as (read-only, the user's rows only).
"""

import argparse
import os
import time
import weakref

from mcp.server.fastmcp import Context, FastMCP

from serving import access, contract, router, tools
from telemetry import log

USER = None                      # set in main() from --user or SL_USER; there is no default

server = FastMCP(
    "yellowhammer-semantic-layer",
    instructions=("Governed metrics for Yellowhammer, a furniture and lighting retailer. "
                  "Call list_metrics, then query_metrics. Use run_readonly_sql only when no "
                  "metric fits; its answers are flagged as not governed."))


@server.tool()
def list_metrics() -> list[dict]:
    """Governed metrics you may use, with descriptions, owner, status and version."""
    return tools.list_metrics(USER)


@server.tool()
def get_dimensions(metric: str) -> dict:
    """Dimensions and entities a metric can be grouped or filtered by, and the cutoffs."""
    return tools.get_dimensions(USER, metric)


# What each MCP session has been answered, so the fallback rule can see whether
# a governed attempt already failed in this conversation. Keyed by the session
# object, not held in one module-level list: over stdio there is one session
# per process, but over streamable HTTP one server holds many sessions (one per
# Mcp-Session-Id), and module-level lists would let one user's failed attempt
# unlock the fallback for another. A WeakKeyDictionary forgets a session when
# it closes. (Over HTTP the identity must be per session too, from the OAuth
# token, rather than the USER this stdio server is started with.)
#
# Sessions are on their way out. The MCP specification of 2026-07-28 drops
# protocol-level sessions (no Mcp-Session-Id): each request stands alone. This
# demo pins mcp 1.30, which follows the 2025-11-25 specification, so a session
# object still exists to key on. On the newer specification, key this state on
# a conversation handle the SERVER mints instead (guide chapter 4): a tool such
# as start_conversation returns an opaque, unguessable id bound to the
# authenticated user, the client passes it back with every call, and the
# server refuses a handle that belongs to someone else or has expired. Never
# accept a handle the client made up: that would let a model reset "a governed
# attempt already failed" whenever it likes.
SESSIONS = weakref.WeakKeyDictionary()


def session_state(ctx):
    """This session's contracts: {"governed": [...], "fallback": [...]}."""
    return SESSIONS.setdefault(ctx.session, {"governed": [], "fallback": []})


@server.tool()
def query_metrics(metric: str, ctx: Context, group_by: list[str] | None = None,
                  filters: dict[str, str] | None = None, start: str | None = None,
                  end: str | None = None, cutoff: str | None = None,
                  question: str = "") -> dict:
    """Query one governed metric. Returns the response contract (value, filters,
    cutoff, caveats, lineage, compiled SQL) or a refusal with its reason.
    Pass the user's question: it is routed first (the server cannot see the
    user's message, only what you send)."""
    started, request = time.perf_counter(), access.start_request()
    route = router.classify(question) if question else None
    narrowed = tools.qualifier_blocked(route, metric)
    if route and (route.path == "refused" or narrowed):
        reason = route.reason if route.path == "refused" else narrowed
        category = "unsupported" if route.path == "refused" else "constrained"
        result = contract.validate(contract.refused(question, USER, reason, category))
        access.audit(user=USER, tool="query_metrics", question=question, path="refused",
                     category=category, reason=reason)
    else:
        result = tools.query_metrics(USER, metric, group_by, filters, start, end, cutoff,
                                     question=question,
                                     category=route.category if route else "supported",
                                     caveats=route.caveats if route else (),
                                     reproduce=bool(route and route.request.get("reproduce")),
                                     unfiltered=route.request.get("unfiltered", ()) if route else (),
                                     keep_version=bool(route and route.request.get("keep_version")),
                                     bound_filters=route.request.get("bound_filters") if route else None)
    session_state(ctx)["governed"].append(result)
    _telemetry(result, started, request)
    return result


@server.tool()
def run_readonly_sql(sql: str, question: str, ctx: Context, cutoff: str | None = None) -> dict:
    """Fallback: one read-only SELECT, checked by the SQL guard and limited to your
    rows and one cutoff. Pass the user's question: it is refused while a governed
    metric fits. The response is flagged path=sql_fallback."""
    started, request = time.perf_counter(), access.start_request()
    state = session_state(ctx)
    blocked = tools.fallback_blocked(router.classify(question), state["governed"], state["fallback"])
    if blocked:                              # an attempted bypass: audited and logged
        result = contract.validate(contract.refused(question, USER, blocked, "constrained"))
        access.audit(user=USER, tool="run_readonly_sql", question=question, path="refused",
                     category="constrained", reason=blocked, sql=sql)
    else:
        result = tools.run_readonly_sql(USER, sql, cutoff, question)
    state["fallback"].append(result)
    _telemetry(result, started, request, "fallback_blocked" if blocked else None)
    return result


def _telemetry(result, started, request, reason=None):
    """One telemetry record per answer, as agent.ask writes. The model is the
    client's, which the server cannot see."""
    log.write(log.record_for(result, (time.perf_counter() - started) * 1000,
                             {"model": "mcp client (not known to the server)"},
                             request_id=request["request_id"], reason=reason))


def main(argv=None):
    global USER
    parser = argparse.ArgumentParser(prog="sl mcp", description="Yellowhammer MCP server (stdio)")
    parser.add_argument("--user", default=os.environ.get("SL_USER"))
    USER = parser.parse_args(argv).user
    if not USER:                             # never guess who is asking
        parser.error("no user: start the server with --user NAME or set SL_USER. "
                     f"Known users: {', '.join(tools.access.load()['users'])}.")
    tools.access.user(USER)                  # fail fast on an unknown user
    server.run()                             # stdio transport


if __name__ == "__main__":
    main()
