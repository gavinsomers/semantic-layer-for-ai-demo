"""Unusual values (guide ch 10): a figure far outside its metric's history gets a caveat."""

from serving import tools, unusual


def september_sales():
    return tools.query_metrics("finance_lead", "goods_value", start="2026-09-01",
                               end="2026-09-30", cutoff="september_close")


def test_a_usual_figure_has_no_unusual_caveat():
    assert not any(c.startswith("Unusual value") for c in september_sales()["caveats"])


def test_a_figure_outside_the_stored_range_is_flagged(monkeypatch):
    usual = unusual.load()
    usual["goods_value"].update(low=0, high=50)              # as if history were tiny
    monkeypatch.setattr(unusual, "load", lambda path=None: usual)
    answer = september_sales()
    assert answer["total"] == 4145 and answer["path"] == "governed"   # a caveat, not a refusal
    flagged = [c for c in answer["caveats"] if c.startswith("Unusual value")]
    assert flagged and "£138.17 a day is outside the usual range for Accepted goods value" in flagged[0]


def test_no_judgement_where_the_history_does_not_fit(monkeypatch):
    usual = unusual.load()
    usual["goods_value"].update(low=0, high=50)
    monkeypatch.setattr(unusual, "load", lambda path=None: usual)
    by_email = tools.query_metrics("finance_lead", "goods_value", filters={"channel": "email"},
                                   start="2026-09-01", end="2026-09-30", cutoff="september_close")
    assert not any(c.startswith("Unusual value") for c in by_email["caveats"])   # other filters
    assert unusual.check("goods_value", {}, 4145) is None                         # no window


def test_ranges_come_from_enough_daily_history():
    found = unusual.compute("goods_value")
    assert found["kind"] == "per_day" and found["days"] >= unusual.MIN_DAYS
    assert found["low"] <= 4145 / 30 <= found["high"]
    assert unusual.kind("stock_on_hand") == "level" and unusual.kind("goods_value_mtd") is None
