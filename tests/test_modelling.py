"""Modelling rules (guide ch 2): fan-out, semi-additive stock, unknown costs, signs."""

import duckdb
import pytest

import sl
from serving import layer, tools


def test_fan_out_test_bites():
    """A naive join to the many-to-many promotions table must fail the build."""
    code, output = sl.break_and_build(*sl.FAN_OUT_BREAK, capture=True)
    assert code != 0
    assert "FAIL" in output
    failed = [line for line in output.splitlines() if " FAIL " in line]
    assert any("unique_fct_order_lines_snapshot_key" in line for line in failed)
    assert any("goods_value_reconciles_with_order_lines" in line for line in failed)


def test_lamp_test_bites():
    code, output = sl.break_and_build(*sl.LAMP_BREAK, capture=True)
    assert code != 0
    assert any("lamp_line_paid_refund_rule_gives_0" in line
               for line in output.splitlines() if " FAIL " in line)


def test_stock_is_the_last_day_not_a_sum_over_days():
    """Five daily observations of furniture: the answer is 4 September's, not their sum."""
    c = tools.query_metrics("finance_lead", "stock_on_hand", ["product_group"],
                            start="2026-08-31", end="2026-09-04", cutoff="friday_review")
    assert {r["product_group"]: r["stock_on_hand"] for r in c["values"]} == {
        "furniture": 29, "lighting": 19}   # 5 armchairs + 20 chairs + 4 sofas; 8 + 11 lamps


def test_sofa_has_zero_available_at_the_friday_review():
    with duckdb.connect(str(layer.WAREHOUSE), read_only=True) as con:
        row = con.execute("select on_hand, reserved, quarantined, available from fct_stock_daily "
                          "where product = 'Sofa' and cutoff_id = 'friday_review'").fetchone()
    assert row == (4, 3, 1, 0)


def test_unknown_cost_stops_contribution_instead_of_counting_as_zero():
    c = tools.query_metrics("finance_lead", "contribution_after_ads", filters={"channel": "organic"},
                            start="2026-09-01", end="2026-09-30", cutoff="september_close")
    assert c["path"] == "refused"
    assert "unknown cost" in c["refusal_reason"]


def test_refunds_are_stored_positive_and_cash_applies_the_sign_once():
    with duckdb.connect(str(layer.WAREHOUSE), read_only=True) as con:
        negative = con.execute("select count(*) from payments where amount <= 0").fetchone()[0]
        collected, refunded = con.execute(
            "select sum(amount) filter (where direction = 'in'), "
            "sum(amount) filter (where direction = 'out') from fct_cash_movements "
            "where cutoff_id = 'friday_review' and channel = 'paid_search'").fetchone()
    assert negative == 0
    assert (collected, refunded) == (1300, 100)


def test_month_to_date_is_cumulative_within_the_month():
    c = tools.query_metrics("finance_lead", "goods_value_mtd", ["metric_time__day"],
                            start="2026-09-01", end="2026-09-30", cutoff="september_close")
    values = [row["goods_value_mtd"] for row in c["values"]]
    assert values == sorted(values) and values[-1] == 4145


def test_a_later_record_is_invisible_at_an_earlier_cutoff():
    """YH-2019 was accepted on 30 September but recorded at 07:10 on 1 October."""
    with duckdb.connect(str(layer.WAREHOUSE), read_only=True) as con:
        cutoffs = {r[0] for r in con.execute(
            "select cutoff_id from fct_order_lines where order_id = 'YH-2019'").fetchall()}
    assert cutoffs == set()


def test_a_request_with_no_rows_is_an_empty_answer_not_a_crash():
    """Found by the live evaluation: mf writes no CSV at all for an empty result."""
    c = tools.query_metrics("finance_lead", "goods_value", ["product_group"],
                            filters={"channel": "paid_search"}, start="2026-09-10",
                            end="2026-09-30", cutoff="september_close")
    assert c["path"] == "governed" and c["values"] == []


def test_a_query_without_a_cutoff_is_refused():
    """One copy of each fact per cutoff: without a cutoff filter every figure is
    counted once per cutoff, so the layer refuses the request."""
    with pytest.raises(layer.LayerError, match="No cutoff"):
        layer.query(["goods_value"], (), [layer.where_clause("channel", "paid_search")])
