"""Fixes for the gaps the blind held-out run of 21 October 2026 found (revision 2,
after the blind result was recorded; the held-out set is spent by them).

One test (or a few) per fix: product filters, version binding, the figure
check and the user's own figures, MetricFlow's column names, advertising
periods a window cuts through, the approved-refund rule in any word order, a
bare follow-up, and the queued follow-ups (caveat wording, reason codes, the
unusual flag, the report's stemming, the MCP session note).
"""

import pytest

from serving import agent, clock, contract, layer, router, tools
from serving.agent import stray_numbers
from serving.router import classify
from telemetry import log, report


@pytest.fixture
def clock_at():
    yield clock.set_as_of
    clock.set_as_of(None)


@pytest.fixture
def v2_from(monkeypatch):
    """Move version 2's effective_from (a patched copy of the metric list)."""
    def move(day, status=None):
        real = layer.metrics

        def moved():
            found = real()
            found["contribution_after_ads_v2"]["meta"]["effective_from"] = day
            if status:
                found["contribution_after_ads_v2"]["meta"]["status"] = status
            return found

        monkeypatch.setattr(layer, "metrics", moved)
    return move


# --- (a) Product filters (h2_03, h2_15) -------------------------------------------

def test_a_product_named_in_the_question_is_a_filter():
    assert classify("How many sofas were free to sell at the 5am stock count on Friday 4 September?"
                    ).request["filters"] == {"product": "Sofa"}
    route = classify("How many dining chairs have we got available right now?")
    assert route.path == "governed" and route.request["metric"] == "stock_available"
    assert route.request["filters"] == {"product": "Dining chair"}


def test_a_product_filter_answers_for_that_product_only():
    result, _ = agent.ask("How many dining chairs have we got available right now?", "finance_lead",
                          offline=True)
    assert result["path"] == "governed" and result["total"] == 16
    assert {"field": "product", "value": "Dining chair", "source": "question"} in result["filters_applied"]
    sofas = tools.query_metrics("finance_lead", "stock_available", filters={"product": "Sofa"},
                                start="2026-09-04", end="2026-09-04", cutoff="friday_review")
    assert sofas["total"] == 0


def test_product_resolves_to_each_models_own_dimension_name():
    assert layer.resolve_dimension("stock_available", "product") == "stock_snapshot__stock_product"
    assert layer.resolve_dimension("goods_value", "product") == "order_line_snapshot__product"
    assert layer.resolve_dimension("goods_value", "channel") == "channel"
    by_product = tools.query_metrics("finance_lead", "goods_value", group_by=["product"],
                                     cutoff="friday_review")
    assert by_product["path"] == "governed" and all("product" in row for row in by_product["values"])


def test_a_metric_that_cannot_be_split_by_product_refuses_the_product_filter():
    result = tools.query_metrics("finance_lead", "contribution_after_ads", filters={"product": "Sofa"})
    assert result["path"] == "refused" and "cannot be split by product" in result["refusal_reason"]


def test_a_product_the_question_names_is_applied_even_if_the_request_leaves_it_out():
    """Live, the model grouped by product instead of filtering, and the answer's
    total was every product's (38) for a question about sofas."""
    route = classify("How many sofas were free to sell at the 5am stock count on Friday 4 September?")
    assert route.request["bound_filters"] == {"product": "Sofa"}
    result = tools.query_metrics("finance_lead", "stock_available", group_by=["product"],
                                 start="2026-09-04", end="2026-09-04", cutoff="friday_review",
                                 bound_filters=route.request["bound_filters"])
    assert result["total"] == 0 and [row["product"] for row in result["values"]] == ["Sofa"]
    assert any("left it out, so it was applied" in c for c in result["caveats"])
    # Two values of one dimension may be a comparison: neither is bound.
    both = classify("Compare furniture's goods value with lighting's in September")
    assert "product_group" not in (both.request.get("bound_filters") or {})


# --- (b) Version binding ------------------------------------------------------------

def test_a_superseded_version_named_for_a_current_question_gets_the_version_in_force(clock_at):
    clock_at("2026-10-06")
    result = tools.query_metrics("finance_lead", "contribution_after_ads",
                                 question="What is paid-search contribution now?")
    assert result["metrics"] == ["contribution_after_ads_v2"] and result["total"] == 0
    assert any(c.startswith("You asked for Provisional contribution after advertising (version 1")
               and "this answer uses it" in c for c in result["caveats"])


def test_the_friday_review_or_the_old_rule_by_name_still_gets_version_1(clock_at):
    clock_at("2026-10-06")
    friday, _ = agent.ask("What did we make on paid search in the Friday review?", "finance_lead",
                          offline=True)
    assert friday["metrics"] == ["contribution_after_ads"] and friday["total"] == 50
    route = classify("What was contribution under the paid refund rule for the trial?")
    assert route.request.get("keep_version") is True
    kept = tools.query_metrics("finance_lead", "contribution_after_ads", keep_version=True)
    assert kept["metrics"] == ["contribution_after_ads"] and kept["total"] == 50


def test_version_2_brought_forward_unannounced_is_bound_too(v2_from):
    """The staleness 'rule' scenario: no hand-written file says version 2 is in force."""
    v2_from("2026-10-01")
    result = tools.query_metrics("finance_lead", "contribution_after_ads", question="Contribution?")
    assert result["metrics"] == ["contribution_after_ads_v2"] and result["total"] == 0
    friday = tools.query_metrics("finance_lead", "contribution_after_ads", reproduce=True)
    assert friday["metrics"] == ["contribution_after_ads"] and friday["total"] == 50


def test_instructions_hold_no_version_date_and_the_prompt_generates_it(v2_from):
    text = agent.INSTRUCTIONS.read_text(encoding="utf-8")
    for date_text in ("5 October", "2026-10-05", "until 5"):
        assert date_text not in text
    prompt = agent.system_prompt(classify("What did we make on paid search in the Friday review?"))
    assert "contribution_after_ads (Provisional contribution after advertising (version 1" in prompt
    assert "takes over on 5 October 2026" in prompt
    v2_from("2026-10-01")
    prompt = agent.system_prompt(classify("What did we make on paid search in the Friday review?"))
    assert "contribution_after_ads_v2 (Provisional" in prompt and "since 1 October 2026" in prompt
    metric = next(m for m in tools.list_metrics("finance_lead") if m["name"] == "contribution_after_ads_v2")
    assert metric["effective_from"] == "2026-10-01" and metric["in_force_today"] is True


# --- (c) The figure check and the user's own figures (h2_11) ------------------------

def test_a_figure_quoted_from_the_question_is_not_stray():
    result = contract.new("Friday's review had paid search at £50. Is that still the number?",
                          "finance_lead", "constrained", "governed", total=0, values=[],
                          metrics=["contribution_after_ads"], compiled_sql="select 1")
    text = "It has moved: the figure is now £0, down from the £50 shown in Friday's review."
    assert stray_numbers(text, result) == set()
    assert stray_numbers("It is now £0, not £60.", result) == {60.0}


def test_negative_caveat_figures_are_read_as_written():
    """json.dumps used to escape '−£70' to '\\u2212\\u00a370', which reads as 2212 and 370."""
    result = contract.new("q", "finance_lead", "supported", "governed", total=0, values=[],
                          caveats=["Lighting was −£70 on Friday."], compiled_sql="select 1")
    assert stray_numbers("Lighting was −£70 on Friday.", result) == set()
    assert stray_numbers("That is 370.", result) == {370.0}


# --- (d) MetricFlow's column names (KeyError in summarise) ---------------------------

def test_a_time_dimension_without_a_grain_does_not_break_the_answer():
    result = tools.query_metrics("finance_lead", "advertising",
                                 group_by=["ad_spend_snapshot__period_start"],
                                 filters={"channel": "paid_search"}, cutoff="september_close")
    assert result["path"] == "governed" and result["total"] == 420
    assert result["values"][0]["ad_spend_snapshot__period_start"].startswith("2026-08-31")
    assert "£420" in result["answer_text"]


# --- (e) An advertising period the window cuts through (h2_14) -----------------------

def test_a_window_that_cuts_through_an_advertising_period_asks_which_window():
    route = classify("What's paid search's contribution after ads for September?")
    assert route.path == "clarify" and route.category == "constrained"
    assert "four days" in route.options[0] and "31 August 2026 to 3 September 2026" in route.options[0]
    assert route.clarifying_question.count("?") == 1
    direct = tools.query_metrics("commercial_lead", "contribution_after_ads", start="2026-09-01",
                                 end="2026-09-30", cutoff="september_close")
    assert direct["path"] == "clarify" and direct["total"] is None


def test_whole_periods_and_unknown_costs_are_not_asked_about():
    assert classify("What did we make on paid search in the Friday review?").path == "governed"
    assert tools.advertising_gap("contribution_after_ads", {"channel": "paid_search"},
                                 "2026-08-31", "2026-09-30", "september_close") is None
    assert tools.advertising_gap("goods_value", {}, "2026-09-01", "2026-09-30", "september_close") is None
    # An unknown cost refuses the selection whatever the window: refusal, not a question.
    result, _ = agent.ask("What was contribution after advertising across all channels in September?",
                          "finance_lead", offline=True)
    assert result["path"] == "refused" and "unknown cost" in result["refusal_reason"]


def test_the_approved_refund_rule_is_recognised_in_any_word_order():
    route = classify("Just as a comparison, what would the paid-search contribution have been on "
                     "Friday if we'd taken off the refunds we'd approved, not just the ones paid?")
    assert route.request["metric"] == "contribution_after_ads_v2" and route.category == "constrained"
    assert classify("What if we count approved refunds?").request["metric"] == "contribution_after_ads_v2"
    assert classify("What did we make on paid search in the Friday review?"
                    ).request["metric"] == "contribution_after_ads"


# --- A bare follow-up ("And for furniture?") ------------------------------------------

def test_a_follow_up_with_a_filter_but_no_metric_asks_which_figure():
    route = classify("And for furniture?")
    assert route.path == "clarify" and "furniture" in route.clarifying_question
    assert {"goods_value", "contribution_after_ads", "stock_available"} <= set(route.options)
    assert "cash_received" not in route.options            # cannot be split by product group
    assert "contribution_after_ads_v2" not in route.options    # not in force on the demo clock
    result, _ = agent.ask("What about lighting?", "finance_lead", offline=True)
    assert result["path"] == "clarify" and result["total"] is None


def test_a_question_with_more_than_a_filter_is_not_a_bare_follow_up():
    assert classify("How many order lines had more than two units in September?").path == "sql_fallback"
    assert classify("How many furniture order lines had more than two units?").path == "sql_fallback"


# --- Queued follow-ups (REVISION-2-FOLLOWUPS.md, demo fixes 1-4 and 6) ---------------

def test_the_effective_from_caveat_says_still_marked_only_for_a_draft(clock_at, v2_from):
    clock_at("2026-10-06")
    draft = tools.query_metrics("finance_lead", "contribution_after_ads_v2")
    assert any("rule for current answers from 5 October 2026" in c and "still marked draft" in c
               for c in draft["caveats"])
    v2_from("2026-10-05", status="certified")
    certified = tools.query_metrics("finance_lead", "contribution_after_ads_v2")
    assert any("rule for current answers from 5 October 2026" in c for c in certified["caveats"])
    assert not any("still marked" in c for c in certified["caveats"])


def test_qualifier_and_on_hold_refusals_have_their_own_reason_codes():
    qualifier = contract.refused("q", "finance_lead", "the question asks only for orders that used "
                                 "a promotion, and SQL is switched off.", "constrained")
    held = contract.refused("q", "finance_lead", "Provisional contribution is on hold until "
                            "2026-10-06 14:00: reason.", "constrained")
    assert log.reason_code(qualifier) == "qualifier" and log.reason_code(held) == "on_hold"
    for result in (qualifier, held):
        log.write(log.record_for(result, 10))           # the schema accepts both codes


def test_an_unusual_value_is_flagged_in_the_telemetry_record():
    result = contract.new("q", "finance_lead", "supported", "governed", total=1e6,
                          caveats=["Unusual value: £1,000,000 is outside the usual range."],
                          lineage=[{"metric": "goods_value", "label": "Goods value", "version": 1,
                                    "owner": "x", "approver": "y", "status": "certified",
                                    "last_reviewed": "2026-10-02"}])
    record = log.write(log.record_for(result, 10))
    assert record["unusual"] == ["goods_value"]
    plain, _ = agent.ask("What did we make on paid search in the Friday review?", "finance_lead",
                         offline=True)
    assert log.record_for(plain, 10)["unusual"] == []


def test_the_report_folds_words_with_the_routers_normalise():
    assert report.words("Orders with promotions?") == report.words("order with promotion")
    assert report.words("categories") == {router.normalise("categories")}


def test_the_mcp_server_notes_the_sessionless_spec_and_a_server_minted_handle():
    source = (layer.ROOT / "serving" / "mcp_server.py").read_text(encoding="utf-8")
    assert "2026-07-28" in source and "conversation handle the SERVER mints" in source
