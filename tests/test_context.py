"""The context files (ch 3) stay in step with the semantic layer."""

import duckdb
import pytest
import yaml

from serving import layer, router, tools

CONTEXT = router.load_context()
METRICS = layer.metrics()


def test_synonyms_and_ambiguity_options_name_real_metrics():
    for name in CONTEXT["synonyms"]["metrics"]:
        assert name in METRICS, name
    for rule in CONTEXT["ambiguity"]["ambiguities"]:
        for option in rule.get("options", []) + [rule.get("default")] * ("default" in rule):
            assert option in METRICS, option


def test_every_metric_carries_its_operating_agreement():
    for metric in METRICS.values():
        meta = metric["meta"]
        for key in ("owner", "approver", "status", "version", "last_reviewed"):
            assert key in meta, f"{metric['name']} has no {key}"
        assert meta["status"] in ("draft", "certified", "deprecated")
        if not meta.get("hidden"):
            assert "Not for" in metric["description"] or "NOT" in metric["description"] \
                or "Not:" in metric["description"], f"{metric['name']} says nothing about when not to use it"


def test_there_are_at_least_five_verified_queries_with_who_when_and_the_answer():
    queries = CONTEXT["verified_queries"]["verified_queries"]
    assert len(queries) >= 5
    for vq in queries:
        assert vq["verified_by"] and vq["verified_at"]
        assert vq.get("expected") and "total" in vq["expected"], f"{vq['id']} has no expected answer"


# The expected answers, recomputed with plain SQL on the marts (not through the layer).
TRUTH = {
    "vq_average_order_value_by_region": """select region, sum(goods_value) / count(distinct order_id)
        from fct_order_lines join dim_customers using (customer_id)
        where cutoff_id = 'september_close' group by rollup (region)""",
    "vq_goods_value_month_to_date": """select strftime(date_day, '%Y-%m-%d 00:00:00'),
        (select sum(goods_value) from fct_order_lines where cutoff_id = 'september_close'
         and accepted_date between '2026-09-01' and date_day)
        from metricflow_time_spine where date_day between '2026-09-01' and '2026-09-30'""",
}


@pytest.mark.parametrize("vq_id", TRUTH)
def test_expected_answers_match_plain_sql(vq_id):
    vq = next(q for q in CONTEXT["verified_queries"]["verified_queries"] if q["id"] == vq_id)
    with duckdb.connect(str(layer.WAREHOUSE), read_only=True) as con:
        truth = {group: float(value) for group, value in con.execute(TRUTH[vq_id]).fetchall()}
    for group, value in vq["expected"].items():
        if value is not None:
            assert abs(truth[None if group == "total" else group] - value) < 0.001, group


@pytest.mark.parametrize("vq", [q for q in CONTEXT["verified_queries"]["verified_queries"]
                                if q.get("expected")], ids=lambda q: q["id"])
def test_verified_queries_still_give_their_verified_answers(vq):
    if vq["kind"] == "sql":
        c = tools.run_readonly_sql("finance_lead", vq["sql"], vq.get("cutoff"))
    else:
        r = vq["request"]
        c = tools.query_metrics("finance_lead", r["metric"], r.get("group_by"), r.get("filters"),
                                r.get("start"), r.get("end"), r.get("cutoff"))
    assert c["path"] in ("governed", "sql_fallback"), c.get("refusal_reason")
    expected = dict(vq["expected"])
    assert c["total"] == expected.pop("total")
    metric = vq.get("request", {}).get("metric")
    for group, value in expected.items():
        row = next(row for row in c["values"] if group in row.values())
        assert row[metric] == value
