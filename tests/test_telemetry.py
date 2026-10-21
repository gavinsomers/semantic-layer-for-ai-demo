"""Telemetry (guide ch 10): the log, a simulated week, the report and its alerts."""

import jsonschema
import pytest

from serving import agent
from telemetry import log, report, simulate


def test_every_answer_writes_one_valid_telemetry_record():
    agent.ask("What did we make on paid search in the Friday review?", "lighting_buyer", offline=True)
    agent.ask("What was our profit last week?", "finance_lead", offline=True)
    agent.ask("Why did lighting fall?", "commercial_lead", offline=True)
    records = log.read()
    assert [r["path"] for r in records] == ["governed", "clarify", "refused"]
    for record in records:
        jsonschema.validate(record, log.SCHEMA)
    governed = records[0]
    assert governed["metrics"] == [{"name": "contribution_after_ads", "version": 1,
                                    "status": "certified"}]
    assert governed["cutoff"] == "friday_review" and governed["model"] == "offline"
    assert "-70" not in str(governed)                  # values are never logged


def test_the_same_answer_has_the_same_fingerprint():
    a, _ = agent.ask("What did we make on paid search in the Friday review?", "finance_lead", offline=True)
    b, _ = agent.ask("What did we make on paid search in the Friday review?", "finance_lead", offline=True)
    c, _ = agent.ask("What did we make on paid search in the Friday review?", "lighting_buyer", offline=True)
    assert log.fingerprint(a) == log.fingerprint(b) != log.fingerprint(c)


@pytest.fixture(scope="module")
def weeks():
    before = simulate.simulate("2026-09-21", seed=7, context=simulate.context_before_promotion())
    after = simulate.simulate("2026-09-28", seed=7, model="assistant-model-b", model_change_day=None)
    return (report.analyse(before, exclude_metrics=["orders_with_promotion"]),
            report.analyse(after), before, after)


def test_simulation_is_deterministic_and_schema_valid(weeks):
    again = simulate.simulate("2026-09-21", seed=7, context=simulate.context_before_promotion())
    assert again == weeks[2] and len(again) == 7 * simulate.PER_DAY
    for record in again[:50]:
        jsonschema.validate(record, log.SCHEMA)


def test_promoting_the_top_cluster_lowers_the_bypass_rate(weeks):
    before, after = weeks[:2]
    top = before["fallback_clusters"][0]
    assert set(top[1][:2]) == {"promotion", "order"} and top[3] == 5
    assert after["bypass_rate"] < before["bypass_rate"] - 0.15
    assert not any("promotion" in c[1][:1] and c[3] > 1 for c in after["fallback_clusters"])


def test_report_finds_unused_metrics_drift_staleness_and_model_change(weeks):
    before = weeks[0]
    assert "stock_on_hand" in before["unused_metrics"]
    assert "orders_with_promotion" not in before["unused_metrics"]      # it did not exist yet
    governed_questions = {d["question"] for d in before["drift"]}
    assert "What did we make on paid search in the Friday review?" not in governed_questions
    raised = report.alerts(before)
    assert any("bypass rate" in a for a in raised)
    assert any("2026-09-23" in a and "hours old" in a for a in raised)      # the missed load
    assert any("model changed" in a for a in raised)


def test_clusters_group_phrasings_by_their_words():
    found = report.clusters(["How many promoted purchases last week?", "Promo code uptake",
                             "Lines with more than two units?", "How many order lines had more than two units?"])
    assert [(c[0], c[3]) for c in found] == [(2, 2), (2, 2)]


def test_week_2_asks_week_1s_questions_on_the_model_week_1_ended_on(weeks):
    before, after = weeks[2], weeks[3]
    asked = lambda records: [(r["user"], r["question"], r["ts"][11:]) for r in records]
    assert asked(before) == asked(after)                 # only the promotion differs
    answered = lambda records: [r["model"] for r in records if r["model"] != "router"]
    assert answered(before)[-1] == "assistant-model-b" and set(answered(after)) == {"assistant-model-b"}


def test_refusals_carry_a_reason_code_that_changes_the_fingerprint():
    denied, _ = agent.ask("How much cash came in for the paid-search orders in the Friday review?",
                          "commercial_lead", offline=True)
    agent.ask("Why did lighting fall?", "finance_lead", offline=True)
    agent.ask("How many order lines had more than two units in September?", "finance_lead",
              offline=True, allow_sql=False)
    assert [r["reason_code"] for r in log.read()] == ["access", "unsupported_question", "no_metric"]
    assert log.fingerprint(denied) != log.fingerprint(denied, "other")


def test_no_metric_refusals_are_clustered_as_coverage_gaps():
    record = lambda q, path, code: {"ts": "2026-09-21T09:00:00+01:00", "user": "finance_lead",
                                    "question": q, "path": path, "reason_code": code, "metrics": [],
                                    "model": "m", "answer_fingerprint": "x", "latency_ms": 1,
                                    "tokens": {"prompt": 3, "completion": 1}, "feedback": None}
    found = report.analyse([record("How many repeat customers in September?", "refused", "no_metric"),
                            record("How many repeat customers last week?", "sql_fallback", None)])
    assert found["fallback_clusters"][0][0] == 2 and found["reasons"] == {"no_metric": 1}
    assert found["tokens_by_path"] == {"refused": 4, "sql_fallback": 4}


def test_drift_is_explained_by_a_data_change_and_mixed_drift_is_partly_explained():
    def record(ts, fingerprint, data_as_of):
        return {"ts": ts, "user": "u", "question": "q", "path": "sql_fallback", "reason_code": None,
                "metrics": [], "model": "m", "answer_fingerprint": fingerprint, "cutoff": "latest",
                "data_as_of": data_as_of, "latency_ms": 1, "tokens": {"prompt": 0, "completion": 0},
                "feedback": None}
    found = report.analyse([record("2026-09-21T09:00", "a", "2026-09-21T06:00"),
                            record("2026-09-22T09:00", "b", "2026-09-22T06:00"),     # new load
                            record("2026-09-22T10:00", "c", "2026-09-22T06:00")])    # same data
    (drift,) = found["drift"]
    assert drift["explained_by"] == "partly explained by a data change" and drift["unexplained"] == 1


def test_the_mcp_server_writes_telemetry_for_a_blocked_fallback(monkeypatch):
    from serving import mcp_server
    monkeypatch.setattr(mcp_server, "USER", "finance_lead")
    ctx = type("Ctx", (), {"session": type("Session", (), {})()})()   # one fake MCP session
    mcp_server.run_readonly_sql("select 1", "What did we make on paid search in the Friday review?", ctx)
    mcp_server.query_metrics("contribution_after_ads", ctx, question="Why did lighting fall?")
    blocked, refused = log.read()
    assert blocked["path"] == "refused" and blocked["reason_code"] == "fallback_blocked"
    assert refused["reason_code"] == "unsupported_question"
    assert blocked["model"].startswith("mcp client")
