"""Chapter 10 demo: a week of traffic, one fallback cluster promoted, the next week.

    uv run python -m sl telemetry-demo

Week 1 runs with the context as it was before the promotions cluster had a
metric. The report shows the cluster. Promoting it means three reviewed
additions, all already in this repository:

    the metric          orders_with_promotion in dbt/models/semantic/semantic_layer.yml
    a verified query    vq_orders_with_promotion_september in context/verified_queries.yml
    an eval case        s10_promotion_orders in evals/cases.yml

plus its synonyms in context/synonyms.yml. Week 2 asks the same questions
(the same seed), on the model week 1 ended on, with the metric in place, so
any fall in the bypass rate comes from the promotion alone.
"""

from pathlib import Path

from telemetry import report, simulate

OUT = Path(__file__).resolve().parent / "out"


def main(_args=None):
    week1 = simulate.simulate("2026-09-21", seed=7, context=simulate.context_before_promotion())
    simulate.write(week1, OUT / "week1.jsonl")
    before = report.analyse(week1, exclude_metrics=[simulate.PROMOTED["metric"]])
    print(report.render(before, "Week 1 (21 to 27 September 2026, synthetic): before promotion"))

    top = before["fallback_clusters"][0]
    print(f"\nPromote the largest cluster [{', '.join(top[1])}], {top[0]} answers:")
    print(f"  + metric        {simulate.PROMOTED['metric']} (dbt/models/semantic/semantic_layer.yml)")
    print(f"  + verified query {simulate.PROMOTED['verified_query']} (context/verified_queries.yml)")
    print("  + eval case     s10_promotion_orders (evals/cases.yml)")
    print("  + synonyms      promo code, discount code, promoted purchases ... (context/synonyms.yml)\n")

    # Same seed, so the same questions; it starts on the model week 1 ended on.
    week2 = simulate.simulate("2026-09-28", seed=7, model="assistant-model-b", model_change_day=None)
    simulate.write(week2, OUT / "week2.jsonl")
    after = report.analyse(week2)
    print(report.render(after, "Week 2 (28 September to 4 October 2026, synthetic): after promotion"))
    print(f"\nBypass rate: {before['bypass_rate']:.1%} -> {after['bypass_rate']:.1%}. "
          f"Records in {OUT.relative_to(OUT.parent.parent)}/.")
    return 0 if after["bypass_rate"] < before["bypass_rate"] else 1
