"""MCP smoke test (ch 4): start the server over stdio and call two tools."""

import asyncio
import json
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from serving.layer import ROOT


async def call(user, calls):
    params = StdioServerParameters(command=sys.executable,
                                   args=["-m", "serving.mcp_server", "--user", user], cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            results = []
            for name, arguments in calls:
                result = await session.call_tool(name, arguments)
                results.append(json.loads(result.content[0].text) if len(result.content) == 1
                               else [json.loads(c.text) for c in result.content])
            return names, results


def test_mcp_server_lists_and_queries_metrics_as_the_signed_in_user():
    question = "What did we make on paid search in the Friday review?"
    names, (metrics, answer) = asyncio.run(call("lighting_buyer", [
        ("list_metrics", {}),
        ("query_metrics", {"metric": "contribution_after_ads", "group_by": ["product_group"],
                           "cutoff": "friday_review", "question": question}),
    ]))
    assert names == {"list_metrics", "get_dimensions", "query_metrics", "run_readonly_sql"}
    metric_names = [m["name"] for m in (metrics if isinstance(metrics, list) else [metrics])]
    assert "contribution_after_ads" in metric_names and "cash_received" not in metric_names
    assert answer["path"] == "governed" and answer["user"] == "lighting_buyer"
    assert answer["total"] == -70


def test_mcp_server_refuses_an_unsupported_question_before_querying():
    _, (answer,) = asyncio.run(call("finance_lead", [
        ("query_metrics", {"metric": "contribution_after_ads", "question": "Why did lighting fall?"}),
    ]))
    assert answer["path"] == "refused" and answer["category"] == "unsupported"


def test_mcp_server_refuses_sql_while_a_governed_metric_fits():
    """The server, not only the agent loop, applies governed first, SQL second (to the
    question the client sends: only the warehouse role enforces it against any client)."""
    question = "What did we make on paid search in the Friday review?"
    _, (first, after) = asyncio.run(call("finance_lead", [
        ("run_readonly_sql", {"sql": "select sum(goods_value) from fct_order_lines",
                              "question": question}),
        ("run_readonly_sql", {"sql": "delete from fct_order_lines",
                              "question": "Delete the order lines"}),
    ]))
    assert first["path"] == "refused" and "query_metrics first" in first["refusal_reason"]
    assert after["path"] == "refused" and after["compiled_sql"] is None   # the guard still refuses


def test_a_refused_fallback_does_not_count_as_a_failed_governed_attempt():
    question = "What did we make on paid search in the Friday review?"
    _, (first, second) = asyncio.run(call("finance_lead", [
        ("run_readonly_sql", {"sql": "select 1", "question": question}),
        ("run_readonly_sql", {"sql": "select sum(goods_value) from fct_order_lines",
                              "question": question}),
    ]))
    assert first["path"] == second["path"] == "refused"
    assert "query_metrics first" in second["refusal_reason"]


def test_the_server_refuses_to_start_without_a_user():
    import os
    import subprocess
    env = {k: v for k, v in os.environ.items() if k != "SL_USER"}
    done = subprocess.run([sys.executable, "-m", "serving.mcp_server"], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 2 and "no user" in done.stderr and "SL_USER" in done.stderr


class FakeSession:
    """Stands in for an MCP ServerSession: one per connection (or HTTP Mcp-Session-Id)."""


def ctx_for(session):
    return type("Ctx", (), {"session": session})()


def test_two_sessions_do_not_share_fallback_state(monkeypatch):
    """A failed governed attempt in one session must not unlock SQL in another
    (it would, with module-level lists, as soon as the server runs over HTTP)."""
    from serving import mcp_server
    monkeypatch.setattr(mcp_server, "USER", "finance_lead")
    question = "What did we make on paid search in the Friday review?"
    a, b = ctx_for(FakeSession()), ctx_for(FakeSession())
    failed = mcp_server.query_metrics("no_such_metric", a, question=question)
    assert failed["path"] == "refused"                      # session A: governed tried and failed
    in_a = mcp_server.run_readonly_sql("select count(*) as n from fct_order_lines", question, a)
    in_b = mcp_server.run_readonly_sql("select count(*) as n from fct_order_lines", question, b)
    assert in_a["path"] == "sql_fallback"
    assert in_b["path"] == "refused" and "query_metrics first" in in_b["refusal_reason"]
    assert len(mcp_server.session_state(a)["governed"]) == 1
    assert mcp_server.session_state(b)["governed"] == []


def test_the_server_will_not_answer_a_narrowed_question_with_a_wider_figure(monkeypatch):
    """c15: 'the goods value of orders that used a promotion' must not get all goods value."""
    from serving import mcp_server
    monkeypatch.setattr(mcp_server, "USER", "finance_lead")
    question = "What was the goods value of orders that used a promotion in September?"
    result = mcp_server.query_metrics("goods_value", ctx_for(FakeSession()), question=question,
                                      start="2026-09-01", end="2026-09-30")
    assert result["path"] == "refused" and result["total"] is None
    assert "orders that used a promotion" in result["refusal_reason"]
