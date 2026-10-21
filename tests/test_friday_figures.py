"""The book's Friday review figures, through the semantic layer (guide ch 2).

If any of these fail, the warehouse no longer tells the book's story.
"""

import duckdb

from serving import layer, tools

TRIAL = dict(filters={"channel": "paid_search"}, start="2026-08-31", end="2026-09-03")


def split(contract, metric):
    return {row["product_group"]: row[metric] for row in contract["values"]}


def test_paid_refund_rule_gives_50():
    c = tools.query_metrics("finance_lead", "contribution_after_ads", ["product_group"],
                            cutoff="friday_review", **TRIAL)
    assert c["path"] == "governed"
    assert c["total"] == 50
    assert split(c, "contribution_after_ads") == {"furniture": 120, "lighting": -70}
    assert c["lineage"][0]["version"] == 1 and c["lineage"][0]["owner"] == "finance lead"
    assert c["cutoff"] == {"id": "friday_review", "at": "2026-09-04T06:00:00+01:00"}   # 05:00 UTC


def test_approved_refund_rule_gives_0_as_a_comparison():
    c = tools.query_metrics("finance_lead", "contribution_after_ads_v2", ["product_group"],
                            cutoff="friday_review", **TRIAL)
    assert c["total"] == 0
    assert split(c, "contribution_after_ads_v2") == {"furniture": 120, "lighting": -120}
    assert c["lineage"][0]["status"] == "draft"
    assert any("comparison, not the rule in force" in note for note in c["caveats"])


def test_cash_received_is_1200_and_the_same_under_either_rule():
    c = tools.query_metrics("finance_lead", "cash_received", cutoff="friday_review", **TRIAL)
    assert c["total"] == 1200


def test_contribution_defaults_are_the_trial_at_the_friday_cutoff():
    """Book ch 4, step 3: the definition's defaults fill what the question leaves open."""
    c = tools.query_metrics("finance_lead", "contribution_after_ads")
    assert c["total"] == 50
    assert {"field": "channel", "value": "paid_search", "source": "default"} in c["filters_applied"]


def test_paid_refund_rule_at_11_september_gives_0():
    """The lamp's £50 was paid on 8 September (recorded 12:00), so it now counts."""
    c = tools.query_metrics("finance_lead", "contribution_after_ads", cutoff="lamp_recalculation", **TRIAL)
    assert c["total"] == 0


def test_lamp_line_is_0_under_paid_rule_and_minus_50_under_approved_rule():
    with duckdb.connect(str(layer.WAREHOUSE), read_only=True) as con:
        rows = con.execute(
            "select quantity, goods_value, contribution_before_ads_paid_rule, "
            "contribution_before_ads_approved_rule from fct_order_lines "
            "where product = 'Brass table lamp' and order_id = 'YH-1002' "
            "and cutoff_id = 'friday_review'").fetchall()
    assert rows == [(2, 200, 0, -50)]


def test_totals_behind_the_50():
    with duckdb.connect(str(layer.WAREHOUSE), read_only=True) as con:
        goods, refunds, estimates = con.execute(
            "select sum(goods_value), sum(refunds_paid), "
            "sum(product_cost_estimate + fulfilment_estimate) from fct_order_lines "
            "where cutoff_id = 'friday_review' and channel = 'paid_search' "
            "and accepted_date between '2026-08-31' and '2026-09-03'").fetchone()
    assert (goods, refunds, estimates) == (1500, 100, 930)
