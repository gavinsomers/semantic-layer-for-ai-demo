"""Access control (ch 7): same question, different users, correctly scoped."""

import json

from serving import agent, tools

QUESTION = "What did we make on paid search in the Friday review?"


def test_two_users_get_different_correctly_scoped_answers():
    finance, _ = agent.ask(QUESTION, "finance_lead", offline=True)
    lighting, _ = agent.ask(QUESTION, "lighting_buyer", offline=True)
    assert finance["total"] == 50
    assert {r["product_group"] for r in finance["values"]} == {"furniture", "lighting"}
    assert lighting["total"] == -70
    assert {r["product_group"] for r in lighting["values"]} == {"lighting"}
    assert {"field": "product_group", "value": "lighting", "source": "access_policy"} \
        in lighting["filters_applied"]
    assert any("not the business-wide total" in c for c in lighting["caveats"])


def test_commercial_lead_has_no_cash_measure():
    c = tools.query_metrics("commercial_lead", "cash_received", cutoff="friday_review")
    assert c["path"] == "refused" and c["access"]["denied"] is True
    assert "1,200" not in c["answer_text"] and "1200" not in c["answer_text"]
    assert "cash_received" not in [m["name"] for m in tools.list_metrics("commercial_lead")]


def test_lighting_buyer_cannot_widen_the_row_filter():
    c = tools.query_metrics("lighting_buyer", "goods_value", filters={"product_group": "furniture"})
    assert c["path"] == "refused" and c["access"]["denied"] is True


def test_lighting_buyer_cannot_use_a_measure_without_product_group():
    c = tools.query_metrics("lighting_buyer", "cash_received", cutoff="friday_review")
    assert c["path"] == "refused"
    assert "cannot be limited that way" in c["refusal_reason"]


def test_sql_fallback_respects_the_same_scope():
    c = tools.run_readonly_sql("lighting_buyer", "select sum(goods_value) as g from fct_order_lines "
                               "where channel = 'paid_search' and accepted_date <= '2026-09-03'",
                               cutoff="friday_review")
    assert c["path"] == "sql_fallback" and c["values"] == [{"g": 320}]


def test_an_access_refusal_cannot_be_bypassed_by_the_sql_fallback():
    denied = tools.query_metrics("commercial_lead", "cash_received")
    result = agent.finish("q", "commercial_lead", "", [denied])
    assert result["path"] == "refused"


def test_audit_log_records_who_path_metric_and_version(audit_log_in_tmp):
    agent.ask(QUESTION, "lighting_buyer", offline=True)
    agent.ask("Why did lighting fall?", "commercial_lead", offline=True)
    records = [json.loads(line) for line in audit_log_in_tmp.read_text().splitlines()]
    governed = [r for r in records if r.get("path") == "governed"][0]
    assert governed["user"] == "lighting_buyer"
    assert governed["metric"] == "contribution_after_ads" and governed["version"] == 1
    assert governed["filters"]["product_group"] == "lighting"
    refused = [r for r in records if r.get("path") == "refused"][0]
    assert refused["user"] == "commercial_lead" and refused["category"] == "unsupported"


def test_an_extra_filter_on_a_verified_question_is_answered_or_refused_openly():
    question = "What did we make on paid search for furniture in the Friday review?"
    finance, _ = agent.ask(question, "finance_lead", offline=True)
    assert finance["total"] == 120 and [r["product_group"] for r in finance["values"]] == ["furniture"]
    lighting, _ = agent.ask(question, "lighting_buyer", offline=True)
    assert lighting["path"] == "refused" and lighting["access"]["denied"] is True


def test_audit_lines_share_a_request_id_and_say_whether_access_refused(audit_log_in_tmp):
    agent.ask("How much cash came in for the paid-search orders in the Friday review?",
              "commercial_lead", offline=True)
    agent.ask(QUESTION, "finance_lead", offline=True)
    denied, allowed = [json.loads(line) for line in audit_log_in_tmp.read_text().splitlines()]
    assert denied["access_denied"] is True and allowed["access_denied"] is False
    assert denied["request_id"] != allowed["request_id"] and allowed["model"] == "offline"


def test_metrics_are_listed_by_name_whatever_the_manifest_order(monkeypatch):
    """dbt does not keep the manifest's metric order between parses; `sl metrics` must."""
    from serving import layer
    real = layer.manifest()
    names = sorted(m["name"] for m in real["metrics"])
    for order in (list(reversed(real["metrics"])), sorted(real["metrics"], key=lambda m: m["type"])):
        monkeypatch.setattr(layer, "manifest", lambda order=order: {**real, "metrics": order})
        assert list(layer.metrics()) == names
        listed = [m["name"] for m in tools.list_metrics("finance_lead")]
        assert listed == sorted(listed)
