"""Time (guide ch 2): facts are UTC instants, cutoffs are UK clock times, answers show UK time."""

from datetime import datetime, timezone

import duckdb

from serving import clock, layer, tools


def warehouse(sql):
    with duckdb.connect(str(layer.WAREHOUSE), read_only=True, config={"TimeZone": "UTC"}) as con:
        return con.execute(sql).fetchall()


def test_a_uk_cutoff_becomes_the_right_instant():
    """06:00 BST on Friday 4 September 2026 is 05:00 UTC."""
    (at,), = warehouse("select cutoff_at from stg_cutoffs where cutoff_id = 'friday_review'")
    assert at == datetime(2026, 9, 4, 5, 0, tzinfo=timezone.utc)
    assert tools.cutoffs()["friday_review"] == "2026-09-04T06:00:00+01:00"


def test_facts_are_stored_with_a_time_zone():
    types = dict(warehouse("select column_name, data_type from information_schema.columns "
                           "where table_name = 'fct_order_lines'"))
    assert types["accepted_at"] == types["recorded_at"] == types["cutoff_at"] == \
        "TIMESTAMP WITH TIME ZONE"
    assert types["accepted_date"] == "DATE"


def test_a_late_evening_order_gets_its_uk_date_not_its_utc_date():
    """An order accepted at 00:30 BST on 1 September is 23:30 UTC on 31 August."""
    rows = warehouse("select order_id, accepted_at, accepted_date from fct_order_lines "
                     "where cutoff_id = 'september_close'")
    for _, at, day in rows:
        assert day == at.astimezone(clock.UK).date()


def test_times_are_shown_in_uk_time_with_the_zone():
    assert clock.uk_time("2026-09-04T05:00:00+00:00") == "06:00 BST on Friday 4 September 2026"
    assert clock.uk_time("2026-10-26T06:00:00+00:00") == "06:00 GMT on Monday 26 October 2026"


def test_the_sql_fallback_shows_uk_time_with_its_offset():
    c = tools.run_readonly_sql("finance_lead", "select min(accepted_at) as first from fct_order_lines",
                               "friday_review")
    assert c["values"][0]["first"].endswith("+01:00")


# --- The demo clock (guide ch 9) ------------------------------------------------

import pytest

from serving import agent
from serving.router import classify


@pytest.fixture
def clock_at():
    """Move the demo clock for one test, then put it back."""
    yield clock.set_as_of
    clock.set_as_of(None)


def test_the_demo_clock_is_fixed_not_the_computers():
    assert clock.today().isoformat() == "2026-10-02"
    assert clock.describe_clock() == "As of 2 October 2026 (demo clock)"


def test_version_1_answers_current_questions_until_5_october(clock_at):
    question = "What was paid-search contribution for the trial's first four days?"
    assert classify(question).request["metric"] == "contribution_after_ads"
    clock_at("2026-10-04")
    assert classify(question).request["metric"] == "contribution_after_ads"
    clock_at("2026-10-06")
    assert clock.describe_clock() == "As of 6 October 2026 (demo clock, set by --as-of)"
    result, _ = agent.ask(question, "finance_lead", offline=True)
    assert result["metrics"] == ["contribution_after_ads_v2"] and result["total"] == 0
    assert any("rule for current answers from 5 October 2026" in c for c in result["caveats"])
    assert not any("A comparison" in c for c in result["caveats"])


def test_after_5_october_the_friday_review_is_still_reproduced_with_version_1(clock_at):
    clock_at("2026-10-06")
    result, _ = agent.ask("What did we make on paid search in the Friday review?", "finance_lead",
                          offline=True)
    assert result["metrics"] == ["contribution_after_ads"] and result["total"] == 50
    assert any("reproduces reports made under it" in c for c in result["caveats"])


def test_before_5_october_version_2_is_only_a_comparison():
    result, _ = agent.ask("What if we count approved refunds?", "finance_lead", offline=True)
    assert result["total"] == 0 and result["category"] == "constrained"
    assert any("A comparison, not the rule in force" in c for c in result["caveats"])


# --- A hold (guide ch 9; book ch 8: 38 hours with current contribution on hold) -----

from serving import layer as _layer

BOOK_HOLD = {"since": "2026-10-05 00:00", "until": "2026-10-06 14:00",
             "reason": "The approved-refund rule took effect at 00:00 on 5 October; its checked "
                       "version ships at 14:00 on 6 October."}


@pytest.fixture
def hold(monkeypatch):
    """Put one metric on hold (a patched copy of the metric list)."""
    def put_on_hold(metric, **window):
        real = _layer.metrics

        def held():
            found = real()
            found[metric]["meta"].update(status="on_hold", hold={**BOOK_HOLD, **window})
            return found

        monkeypatch.setattr(_layer, "metrics", held)
    return put_on_hold


def test_the_books_hold_refuses_current_contribution_with_its_reason(hold, clock_at):
    """5 October: version 2 is the rule, but its checked version is not out yet."""
    hold("contribution_after_ads_v2")
    clock_at("2026-10-05")
    result, _ = agent.ask("What was paid-search contribution for the trial's first four days?",
                          "commercial_lead", offline=True)
    assert result["path"] == "refused" and result["total"] is None
    assert "on hold until 2026-10-06 14:00" in result["refusal_reason"]
    assert "checked version ships at 14:00 on 6 October" in result["refusal_reason"]
    friday, _ = agent.ask("What did we make on paid search in the Friday review?", "finance_lead",
                          offline=True)
    assert friday["total"] == 50                               # the past report still reproduces
    cash, _ = agent.ask("How much cash came in for the paid-search orders in the Friday review?",
                        "finance_lead", offline=True)
    assert cash["total"] == 1200                               # unaffected metrics keep answering


def test_a_held_metric_still_reproduces_a_past_report(hold):
    hold("contribution_after_ads", since="2026-10-01", until="2026-10-03",
         reason="A cost feed is being reloaded.")
    current, _ = agent.ask("What was paid-search contribution for the trial's first four days?",
                           "finance_lead", offline=True)
    assert current["path"] == "refused" and "A cost feed is being reloaded." in current["refusal_reason"]
    report, _ = agent.ask("What did we make on paid search in the Friday review?", "finance_lead",
                          offline=True)
    assert report["total"] == 50 and report["lineage"][0]["status"] == "on_hold"
    assert any("On hold for current answers" in c for c in report["caveats"])


def test_a_hold_ends_on_its_date(hold, clock_at):
    hold("contribution_after_ads", since="2026-10-01", until="2026-10-03")
    clock_at("2026-10-04")
    after = tools.query_metrics("finance_lead", "contribution_after_ads", ["product_group"])
    assert after["total"] == 50
