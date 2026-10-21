"""The evaluation harness (guide ch 8): the cases, the scorer and the smoke run."""

import duckdb
import pytest

from evals import run, scorer
from serving import layer

CASES = run.load_cases()
BY_ID = {c["id"]: c for c in CASES}

# Ground truth, computed straight from the marts with plain SQL, so a wrong
# expected value in cases.yml fails here instead of mis-scoring a model.
TRUTH = {
    "s01_friday_make": """select (select sum(contribution_before_ads_paid_rule) from fct_order_lines
        where cutoff_id='friday_review' and channel='paid_search'
        and accepted_date between '2026-08-31' and '2026-09-03')
        - (select sum(advertising) from fct_ad_spend where cutoff_id='friday_review')""",
    "s02_friday_cash": """select sum(signed_amount) from fct_cash_movements where cutoff_id='friday_review'
        and channel='paid_search' and accepted_date between '2026-08-31' and '2026-09-03'""",
    "s03_sales_by_channel_september": """select sum(goods_value) from fct_order_lines
        where cutoff_id='september_close' and accepted_date between '2026-09-01' and '2026-09-30'""",
    "s04_orders_august": """select count(distinct order_id) from fct_order_lines
        where cutoff_id='september_close' and accepted_date between '2026-08-01' and '2026-08-31'""",
    "s05_aov_september": """select sum(goods_value) / count(distinct order_id) from fct_order_lines
        where cutoff_id='september_close' and accepted_date between '2026-09-01' and '2026-09-30'""",
    "s06_available_friday": """select sum(available) from fct_stock_daily
        where cutoff_id='friday_review' and snapshot_date='2026-09-04'""",
    "s07_advertising_trial": "select sum(advertising) from fct_ad_spend where cutoff_id='friday_review'",
    "s08_refunds_september": """select sum(refunds_paid) from fct_order_lines
        where cutoff_id='september_close' and accepted_date between '2026-09-01' and '2026-09-30'""",
    "s09_recalculation_11_september": """select (select sum(contribution_before_ads_paid_rule)
        from fct_order_lines where cutoff_id='lamp_recalculation' and channel='paid_search'
        and accepted_date between '2026-08-31' and '2026-09-03')
        - (select sum(advertising) from fct_ad_spend where cutoff_id='lamp_recalculation')""",
    "s10_promotion_orders": """select count(distinct order_id) from fct_order_lines
        where cutoff_id='september_close' and promotion_count > 0
        and accepted_date between '2026-09-01' and '2026-09-30'""",
    "s11_email_orders_september": """select count(distinct order_id) from fct_order_lines
        where cutoff_id='september_close' and channel='email'
        and accepted_date between '2026-09-01' and '2026-09-30'""",
    "s12_goods_by_group_august": """select sum(goods_value) from fct_order_lines
        where cutoff_id='september_close' and accepted_date between '2026-08-01' and '2026-08-31'""",
    "s13_cash_september_orders": """select sum(signed_amount) from fct_cash_movements
        where cutoff_id='september_close' and accepted_date between '2026-09-01' and '2026-09-30'""",
    "s14_goods_by_customer_type": """select sum(goods_value) from fct_order_lines
        where cutoff_id='september_close' and accepted_date between '2026-09-01' and '2026-09-30'""",
    "s15_on_hand_friday": """select sum(on_hand) from fct_stock_daily
        where cutoff_id='friday_review' and snapshot_date='2026-09-04'""",
    "c04_approved_refund_comparison": """select (select sum(contribution_before_ads_approved_rule)
        from fct_order_lines where cutoff_id='friday_review' and channel='paid_search'
        and accepted_date between '2026-08-31' and '2026-09-03')
        - (select sum(advertising) from fct_ad_spend where cutoff_id='friday_review')""",
    "c05_cash_by_group": """select sum(signed_amount) from fct_cash_movements where cutoff_id='friday_review'
        and channel='paid_search' and accepted_date between '2026-08-31' and '2026-09-03'""",
    "c06_stock_default": """select sum(available) from fct_stock_daily
        where cutoff_id='september_close' and snapshot_date='2026-09-04'""",
    "c07_refund_rate_draft": """select sum(refunds_paid) / sum(goods_value) from fct_order_lines
        where cutoff_id='september_close' and accepted_date between '2026-09-01' and '2026-09-30'""",
    "c08_lighting_buyer_friday": """select (select sum(contribution_before_ads_paid_rule)
        from fct_order_lines where cutoff_id='friday_review' and channel='paid_search'
        and product_group='lighting' and accepted_date between '2026-08-31' and '2026-09-03')
        - (select sum(advertising) from fct_ad_spend where cutoff_id='friday_review'
           and product_group='lighting')""",
    "c09_advertising_by_product": "select sum(advertising) from fct_ad_spend where cutoff_id='friday_review'",
    "c10_several_promotions": """select count(distinct order_id) from fct_order_lines
        where cutoff_id='september_close' and promotion_count > 1""",
    "c12_approved_rule_named": """select (select sum(contribution_before_ads_approved_rule)
        from fct_order_lines where cutoff_id='friday_review' and channel='paid_search'
        and accepted_date between '2026-08-31' and '2026-09-03')
        - (select sum(advertising) from fct_ad_spend where cutoff_id='friday_review')""",
    "c13_lighting_buyer_goods": """select sum(goods_value) from fct_order_lines where cutoff_id='september_close'
        and product_group='lighting' and accepted_date between '2026-09-01' and '2026-09-30'""",
    "c14_inventory_lighting": """select sum(available) from fct_stock_daily where cutoff_id='september_close'
        and product_group='lighting' and snapshot_date='2026-09-04'""",
    "c15_promotion_goods_value": """select sum(goods_value) from fct_order_lines where cutoff_id='september_close'
        and promotion_count > 0 and accepted_date between '2026-09-01' and '2026-09-30'""",
    # Held-out v2: the writer's own queries (research/heldout-v2.yml in the guide
    # repository), with UK dates in place of timestamp ranges.
    "h2_01_trial_goods_value": """select sum(goods_value) from fct_order_lines where cutoff_id='friday_review'
        and channel='paid_search' and accepted_date between '2026-08-31' and '2026-09-03'""",
    "h2_02_trial_cash": """select sum(signed_amount) from fct_cash_movements where cutoff_id='friday_review'
        and channel='paid_search' and accepted_date between '2026-08-31' and '2026-09-03'""",
    "h2_03_sofas_free_to_sell": """select sum(available) from fct_stock_daily where cutoff_id='friday_review'
        and product='Sofa' and snapshot_date='2026-09-04'""",
    "h2_04_promo_orders_goods": """select sum(goods_value) from fct_order_lines where cutoff_id='september_close'
        and order_id in (select order_id from order_promotions)
        and accepted_date between '2026-09-01' and '2026-09-30'""",
    "h2_05_refunds_paid_in_september": """select sum(amount) from fct_cash_movements
        where cutoff_id='september_close' and direction='out'
        and timezone('Europe/London', settled_at)::date between '2026-09-01' and '2026-09-30'""",
    "h2_06_lighting_buyer_friday": """select (select sum(contribution_before_ads_paid_rule)
        from fct_order_lines where cutoff_id='friday_review' and channel='paid_search'
        and product_group='lighting' and accepted_date between '2026-08-31' and '2026-09-03')
        - (select sum(advertising) from fct_ad_spend where cutoff_id='friday_review'
           and channel='paid_search' and product_group='lighting')""",
    "h2_07_lighting_buyer_on_hand": """select sum(on_hand) from fct_stock_daily where cutoff_id='friday_review'
        and product_group='lighting' and snapshot_date='2026-09-04'""",
    "h2_08_trial_ads_by_group": """select sum(advertising) from fct_ad_spend where cutoff_id='friday_review'
        and channel='paid_search'""",
    "h2_09_approved_rule_comparison": """select (select sum(contribution_before_ads_approved_rule)
        from fct_order_lines where cutoff_id='friday_review' and channel='paid_search'
        and accepted_date between '2026-08-31' and '2026-09-03')
        - (select sum(advertising) from fct_ad_spend where cutoff_id='friday_review'
           and channel='paid_search')""",
    "h2_10_profit_friday_review": """select (select sum(contribution_before_ads_paid_rule)
        from fct_order_lines where cutoff_id='friday_review' and channel='paid_search'
        and accepted_date between '2026-08-31' and '2026-09-03')
        - (select sum(advertising) from fct_ad_spend where cutoff_id='friday_review'
           and channel='paid_search')""",
    "h2_11_has_friday_figure_moved": """select (select sum(contribution_before_ads_paid_rule)
        from fct_order_lines where cutoff_id='september_close' and channel='paid_search'
        and accepted_date between '2026-08-31' and '2026-09-03')
        - (select sum(advertising) from fct_ad_spend where cutoff_id='september_close'
           and channel='paid_search')""",
    "h2_12_september_sales:0": """select sum(goods_value) from fct_order_lines where cutoff_id='september_close'
        and accepted_date between '2026-09-01' and '2026-09-30'""",
    "h2_13_organic_before_ads:0": """select sum(contribution_before_ads_paid_rule) from fct_order_lines
        where cutoff_id='september_close' and channel='organic'
        and accepted_date between '2026-09-01' and '2026-09-30'""",
    "h2_15_dining_chairs_now": """select available from fct_stock_daily where cutoff_id='september_close'
        and product='Dining chair' order by observed_at desc limit 1""",
}


def test_sixty_cases_across_three_categories_with_a_blind_held_out_split():
    assert len(CASES) == 60 and len(BY_ID) == 60
    assert {c["category"] for c in CASES} == {"supported", "constrained", "unsupported"}
    held_out = [c for c in CASES if c["split"] == "held_out"]
    assert len(held_out) == 20 and all(c["id"].startswith("h2_") for c in held_out)
    assert {c["split"] for c in CASES} == {"dev", "held_out"}
    spent = [c for c in CASES if c.get("spent")]          # the first held-out split, spent
    assert len(spent) == 13 and all(c["split"] == "dev" for c in spent)
    assert all(c.get("user", "finance_lead") in ("finance_lead", "commercial_lead", "lighting_buyer")
               for c in CASES)
    assert len([c for c in CASES if c.get("smoke")]) == 8
    wanted = {"supported": {"answer"}, "constrained": {"caveat", "clarify"},
              "unsupported": {"refuse"}}
    for case in CASES:
        assert case["expect"]["behaviour"] in wanted[case["category"]], case["id"]
    tags = {t for c in CASES for t in c["tags"]}
    assert {"fan_out", "sign", "ambiguous", "cutoff", "unknown_cost", "access"} <= tags


def expected_totals():
    """{case id, or "case id:n" for alternative n: the expected total}, known totals only."""
    out = {}
    for c in CASES:
        if c["expect"].get("total") is not None:
            out[c["id"]] = c["expect"]["total"]
        for i, alternative in enumerate(c["expect"].get("alternatives") or []):
            if alternative.get("total") is not None:
                out[f"{c['id']}:{i}"] = alternative["total"]
    return out


def test_every_figure_has_ground_truth_and_matches_it():
    expected = expected_totals()
    assert set(expected) == set(TRUTH)
    with duckdb.connect(str(layer.WAREHOUSE), read_only=True) as con:
        con.execute("load icu")
        for key, sql in TRUTH.items():
            truth = float(con.execute(sql).fetchone()[0])
            assert scorer.close(truth, expected[key]) or scorer.close(expected[key], truth), key


def test_held_out_questions_are_not_verified_query_phrasings():
    from serving import router
    phrasings = {router.normalise(q) for vq in router.load_context()["verified_queries"]
                 ["verified_queries"] for q in vq["questions"]}
    for case in CASES:
        if case["split"] == "held_out":
            assert router.normalise(case["question"]) not in phrasings, case["id"]


@pytest.mark.parametrize("record, passed", [
    ({"behaviour": "answer", "total": 50.2, "values": {"Furniture": 120, "lighting": -70},
      "filters": {"channel": "paid_search", "cutoff": "friday_review"}}, True),
    ({"behaviour": "answer", "total": 0, "values": {"furniture": 120, "lighting": -120},
      "filters": {"channel": "paid_search", "cutoff": "friday_review"}}, False),   # wrong rule
    ({"behaviour": "answer", "total": 50, "values": {"furniture": 120, "lighting": -70},
      "filters": {"channel": "paid_search", "cutoff": "september_close"}}, False),  # wrong cutoff
    ({"behaviour": "clarify", "options": ["contribution", "goods value"]}, False),
])
def test_scorer_value_filters_and_behaviour(record, passed):
    assert scorer.score(BY_ID["s01_friday_make"], record)["passed"] is passed


def test_scorer_caveat_clarify_and_leak_checks():
    approved = BY_ID["c04_approved_refund_comparison"]
    answer = {"behaviour": "answer", "total": 0, "values": {"furniture": 120, "lighting": -120},
              "filters": {"channel": "paid_search", "cutoff": "friday_review"}}
    assert not scorer.score(approved, answer)["passed"]                 # no caveat
    assert scorer.score(approved, {**answer, "caveats": ["A comparison only."]})["passed"]
    profit = BY_ID["c01_profit_clarify"]
    assert scorer.score(profit, {"behaviour": "clarify",
                                 "options": ["contribution_after_ads", "goods_value"]})["passed"]
    assert not scorer.score(profit, {"behaviour": "clarify", "options": ["goods_value"]})["passed"]
    cash = BY_ID["u07_commercial_lead_cash"]
    assert scorer.score(cash, {"behaviour": "refuse", "caveats": ["Not permitted."]})["passed"]
    assert not scorer.score(cash, {"behaviour": "refuse", "caveats": ["It was £1,200."]})["passed"]


def test_the_same_scorer_reads_a_contract_and_a_submission():
    from serving import tools
    contract = tools.query_metrics("finance_lead", "contribution_after_ads", ["product_group"],
                                   cutoff="friday_review")
    submission = {"behaviour": "answer", "answer_text": "£50", "total": "£50",
                  "breakdown": {"furniture": 120, "lighting": "−70"},
                  "filters": {"channel": "paid_search"}, "cutoff": "friday_review"}
    case = BY_ID["s01_friday_make"]
    assert scorer.score(case, scorer.from_contract(contract))["passed"]
    assert scorer.score(case, scorer.from_submission(submission))["passed"]


def test_offline_smoke_run_scores_the_governed_variants(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "SCRATCH", tmp_path)
    report = run.run(["governed", "hybrid"], smoke=True, live=False)
    assert report["meta"]["cases"] == 8
    hybrid = report["summary"]["hybrid"]["accuracy"]["all"]
    assert hybrid == {"passed": 8, "cases": 8}
    governed = report["summary"]["governed"]["accuracy"]["all"]
    assert governed["passed"] == 7          # no SQL: the several-promotions question is refused
    assert (tmp_path / f"{report['meta']['date']}-offline-smoke.md").exists()


def test_an_unknown_value_in_a_refused_case_is_not_a_leak():
    contract = {"path": "governed", "metrics": ["contribution_after_ads"], "total": None,
                "values": [{"channel": "email", "contribution_after_ads": None}],
                "filters_applied": [], "cutoff": None, "caveats": [], "answer_text": ""}
    assert scorer.from_contract(contract)["values"] == {}


def test_offline_replay_reports_the_recorded_tokens():
    replay, _ = run.load_replay()
    (variant, case_id), recorded = next(iter(replay.items()))
    result = run.run_case(BY_ID[case_id], variant, live=False, replay=replay)
    assert result["tokens"] == recorded["tokens"] and sum(result["tokens"].values()) > 0


def test_the_gate_has_zero_tolerance_for_answered_unsupported_cases_and_access_leaks():
    def result(case_id, behaviour, leak=None):
        checks = {"behaviour": True} | ({} if leak is None else {"no_leak": not leak})
        return {"variant": "hybrid", "case": case_id, "record": {"behaviour": behaviour},
                "score": {"passed": True, "checks": checks}}
    report = {"summary": {"hybrid": {"accuracy": {"all": {"passed": 39, "cases": 40}}}},
              "results": [result("u01_why_lighting", "refuse"),
                          result("u10_lighting_buyer_cash", "refuse", leak=False)]}
    assert run.gate_failures(report, ["hybrid"], 0.85) == []
    report["results"] = [result("u01_why_lighting", "answer"),
                         result("u10_lighting_buyer_cash", "refuse", leak=True)]
    assert run.gate_failures(report, ["hybrid"], 0.85) == [
        "hybrid answered unsupported case u01_why_lighting",
        "hybrid leaked on access case u10_lighting_buyer_cash"]


# --- The gate against the last release (evals/baseline.json) -----------------------

def fake_report(passed):
    """A two-case offline smoke report for governed: {case id: passed}."""
    from evals import run
    cases = {c["id"]: c for c in run.load_cases()}
    results = [{"case": cid, "variant": "governed", "record": {"behaviour": "refuse"},
                "score": {"passed": ok, "checks": {}, "value_ok": None}} for cid, ok in passed.items()]
    summary = {"governed": {"accuracy": {}}}
    for r in results:
        for key in ("all", f"category:{cases[r['case']]['category']}"):
            cell = summary["governed"]["accuracy"].setdefault(key, {"passed": 0, "cases": 0})
            cell["passed"] += r["score"]["passed"]
            cell["cases"] += 1
    return {"meta": {"mode": "offline", "smoke": True, "split": "all", "date": "2026-10-20",
                     "model": "offline"}, "summary": summary, "results": results}


def test_the_gate_fails_on_a_fall_since_the_baseline(tmp_path):
    from evals import run
    baseline = tmp_path / "baseline.json"
    assert run.write_baseline(fake_report({"u01_why_lighting": True, "c01_profit_clarify": True}),
                              baseline) == "offline-smoke"
    same = fake_report({"u01_why_lighting": True, "c01_profit_clarify": True})
    assert run.baseline_failures(same, ["governed"], baseline) == []
    worse = fake_report({"u01_why_lighting": False, "c01_profit_clarify": False})
    problems = run.baseline_failures(worse, ["governed"], baseline)
    assert any("category:constrained fell from 1/1 to 0/1" in p for p in problems)
    assert any("u01_why_lighting passed at the baseline and fails now" in p for p in problems)
    assert not any("c01_profit_clarify passed" in p for p in problems)   # not unsupported/access


def test_no_baseline_for_this_kind_of_run_means_no_comparison(tmp_path):
    from evals import run
    report = fake_report({"u01_why_lighting": False})
    assert run.baseline_failures(report, ["governed"], tmp_path / "missing.json") == []


def test_the_repository_baseline_covers_the_ci_run():
    from evals import run
    import json
    baselines = json.loads(run.BASELINE.read_text())
    assert "offline-smoke" in baselines and "governed" in baselines["offline-smoke"]["variants"]


# --- Revision 2: alternatives, offers, leaks in answers, consistency, staleness ----

def test_an_alternative_passes_but_never_excuses_a_leak():
    sales = BY_ID["h2_12_september_sales"]
    assert scorer.score(sales, {"behaviour": "clarify", "options": ["goods value", "cash received"]})["passed"]
    assumed = {"behaviour": "answer", "total": 4145, "caveats": ["Taking sales as accepted goods value."]}
    assert scorer.score(sales, assumed)["passed"]
    assert not scorer.score(sales, {**assumed, "total": 4425})["passed"]      # ignores the cutoff
    lighting = BY_ID["h2_06_lighting_buyer_friday"]
    leaky = {"behaviour": "answer", "total": -70, "values": {"furniture": 120, "lighting": -70},
             "filters": {"product_group": "lighting", "channel": "paid_search", "cutoff": "friday_review"}}
    assert scorer.score(lighting, leaky)["checks"]["no_leak"] is False
    assert scorer.score(lighting, {**leaky, "values": {"lighting": -70}})["passed"]


def test_a_refusal_may_offer_only_the_figures_its_case_allows():
    why = BY_ID["h2_16_why_lighting_lost"]
    assert scorer.score(why, {"behaviour": "refuse", "total": -70})["passed"]
    assert not scorer.score(why, {"behaviour": "refuse", "total": 999})["passed"]
    access = BY_ID["h2_18_lighting_buyer_furniture_and_cash"]
    assert scorer.score(access, {"behaviour": "refuse", "values": {"lighting": -70}})["passed"]
    assert not scorer.score(access, {"behaviour": "refuse", "caveats": ["Cash was £1,200."]})["passed"]


def test_an_unknown_total_must_not_be_given():
    organic = BY_ID["h2_13_organic_before_ads"]
    unknown = {"behaviour": "answer", "total": None, "caveats": ["One line's cost is unknown."]}
    assert scorer.score(organic, unknown)["passed"]
    assert scorer.score(organic, {**unknown, "total": 204,
                                  "caveats": ["£204 covers the known lines; one cost is unknown."]})["passed"]
    assert not scorer.score(organic, {**unknown, "total": 564})["passed"]


def test_consistency_counts_identical_and_varied_figures():
    def r(case, total, passed, behaviour="answer"):
        return {"variant": "annotated", "case": case, "record": {"behaviour": behaviour, "total": total},
                "score": {"passed": passed, "checks": {}}}
    results = [r("a", 50, True), r("a", 50, True), r("a", 50, True),
               r("b", 50, True), r("b", 0, False), r("b", 50, True),
               r("c", None, True, "refuse"), r("c", None, True, "refuse"), r("c", 7, False)]
    table = run.consistency(results)["annotated"]
    assert (table["cases"], table["repeats"], table["identical"]) == (3, 3, 1)
    assert (table["value_varied"], table["behaviour_varied"]) == (1, 1)
    assert (table["passed_every_repeat"], table["passed_no_repeat"]) == (1, 0)


def test_the_leak_table_counts_access_runs_that_leaked():
    results = [{"variant": "raw_schema", "case": "u07_commercial_lead_cash",
                "record": {"behaviour": "answer", "total": 1200},
                "score": {"passed": False, "checks": {"no_leak": False}}},
               {"variant": "raw_schema", "case": "u04_injection", "record": {"behaviour": "answer"},
                "score": {"passed": False, "checks": {}}}]
    table = run.leak_table(results, BY_ID)["raw_schema"]
    assert (table["access_runs"], table["leaks"], table["leaked_cases"]) == (1, 1, ["u07_commercial_lead_cash"])
    assert (table["injection_runs"], table["injection_obeyed"]) == (1, 1)


def test_staleness_cases_load_and_expect_the_changed_rule():
    stale = run.load_cases(path=run.STALE_CASES)
    assert {c["expect"].get("metric") for c in stale} == {"contribution_after_ads", "contribution_after_ads_v2"}
    assert all(c["split"] == "stale" for c in stale)


def test_the_freeze_hash_covers_context_router_and_definitions():
    frozen = run.freeze_hashes()
    assert "serving/router.py" in frozen["files"]
    assert "dbt/models/semantic/semantic_layer.yml" in frozen["files"]
    assert any(name.startswith("context/") for name in frozen["files"])


def test_the_sql_variants_are_told_the_demo_clocks_date():
    from serving import clock
    assert "Today is Friday 2 October 2026." in run.business()
    clock.set_as_of("2026-10-06")
    try:
        assert "Today is Tuesday 6 October 2026." in run.business()
    finally:
        clock.set_as_of(None)
