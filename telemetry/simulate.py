"""A week of synthetic traffic, written as telemetry (guide chapter 10).

    uv run python -m sl telemetry-demo

SYNTHETIC. No Yellowhammer assistant traffic exists; these records only
exercise the report. Each simulated question is routed by the real router
(serving/router.py) against the real context and access rules, so the PATH
each question takes (governed, sql_fallback, clarify, refused) and the metric
and version that would answer are what the code would really do. Latency,
tokens, feedback, the model id and, for SQL fallback answers, whether the
model's SQL came out the same as last time are drawn from seeded random
generators, so every run gives the same week. Two generators: one picks the
questions, users and times, the other the variance (latency, feedback, SQL).
So the same seed asks the same questions in both weeks, and the promotion is
the only difference between them.

Questions about the current period read the nightly load ("latest"); named
cutoffs such as friday_review are historical by design.
"""

import copy
import hashlib
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

from serving import access, layer, router, tools
from telemetry import log

# (weight, question). The mix is fixed; only the context changes between weeks.
MIX = [
    # Answered by governed metrics.
    (8, "What did we make on paid search in the Friday review?"),
    (6, "How much cash came in for the paid-search orders in the Friday review?"),
    (6, "What were sales by channel in September?"),
    (4, "What was the average order value in September?"),
    (4, "How many orders did we accept in September?"),
    (3, "What is AOV by region?"),
    (3, "What were refunds paid in September?"),
    (3, "What if we count approved refunds?"),
    (3, "How many units were available to sell by product group at the Friday review?"),
    # Clarified or refused before any query.
    (3, "What was our profit last week?"),
    (2, "What was revenue in September?"),
    (2, "Why did lighting fall?"),
    (1, "Should we keep paid search?"),
    # Fallback cluster 1: promotions (promoted to a governed metric after week 1).
    (5, "How many purchases used a discount code in September?"),
    (4, "How many promoted purchases did we have last week?"),
    (3, "Promo code uptake in September"),
    (3, "How many baskets had a promotion code applied in September?"),
    (2, "How many orders used more than one promotion?"),
    # Fallback cluster 2: multi-unit lines (no metric yet).
    (3, "How many order lines had more than two units in September?"),
    (2, "Lines with more than two units last week?"),
    # Fallback cluster 3: repeat customers (no metric yet).
    (2, "How many repeat customers did we have in September?"),
    (2, "Which customers came back for a second purchase?"),
]
USERS = [("finance_lead", 4), ("commercial_lead", 4), ("lighting_buyer", 2)]
PROMOTED = {"metric": "orders_with_promotion", "verified_query": "vq_orders_with_promotion_september"}
PER_DAY = 60


def context_before_promotion():
    """The context as it was before the promotions cluster became a metric."""
    context = copy.deepcopy(router.load_context())
    context["synonyms"]["metrics"].pop(PROMOTED["metric"], None)
    context["verified_queries"]["verified_queries"] = [
        vq for vq in context["verified_queries"]["verified_queries"]
        if vq["id"] != PROMOTED["verified_query"]]
    return context


def path_for(question, user, context):
    """What the real router and access rules would do:
    (category, path, metric or None, reason code or None)."""
    route = router.classify(question, context)
    metric = route.request.get("metric") if route.path == "governed" else None
    if metric:
        policy = access.user(user)
        if metric in policy["deny_metrics"] or not tools._row_filters_fit(policy, metric):
            return route.category, "refused", None, "access"
    reason = "unsupported_question" if route.path == "refused" else None
    return route.category, route.path, metric, reason


def simulate(start, days=7, seed=7, context=None, model="assistant-model-a",
             new_model="assistant-model-b", model_change_day=4, missed_load_day=2):
    """Records for `days` days from `start` (a date string). The model is `model`
    until model_change_day, then `new_model` (model_change_day=None: no change)."""
    rng = random.Random(seed)                  # which questions: the same every week
    noise = random.Random(seed + 10_000)       # latency, feedback and SQL variance
    context = context or router.load_context()
    metrics = layer.metrics()
    day0 = datetime.fromisoformat(start)
    weights = [w for w, _ in MIX]
    sql_variants = {}                  # question -> the fingerprint of the SQL last written
    records = []
    for day in range(days):
        changed = model_change_day is not None and day >= model_change_day
        today_model = new_model if changed else model
        load = day0 + timedelta(days=day, hours=6)
        if day == missed_load_day:     # the nightly load failed: yesterday's data stands
            load -= timedelta(days=1)
        for _ in range(PER_DAY):
            question = rng.choices([q for _, q in MIX], weights)[0]
            user = rng.choices([u for u, _ in USERS], [w for _, w in USERS])[0]
            category, path, metric, reason = path_for(question, user, context)
            ts = day0 + timedelta(days=day, hours=8, minutes=rng.randrange(0, 600))
            roll = noise.random()
            meta = metrics[metric]["meta"] if metric else {}
            if path == "governed":     # deterministic: same request, same answer
                said = f"{metric}|v{meta.get('version')}|{user}|{question}"
            elif path == "sql_fallback":
                key = (question, user)
                # A model writes the same SQL most of the time; a new model often differs.
                if key not in sql_variants or noise.random() < (0.5 if changed else 0.15):
                    sql_variants[key] = noise.randrange(3)
                said = f"sql|{question}|{user}|{sql_variants[key]}"
            else:
                said = f"{path}|{question}"
            cost = {"governed": (4200, 2900), "sql_fallback": (9800, 8600)}.get(path, (40, 0))
            feedback_down = {"governed": 0.04, "sql_fallback": 0.25}.get(path, 0.12)
            records.append({
                "ts": ts.isoformat(timespec="seconds") + "+01:00",
                "answer_id": hashlib.sha256(f"{seed}{day}{len(records)}".encode()).hexdigest()[:12],
                "user": user, "question": question, "category": category, "path": path,
                "reason_code": reason,
                "metrics": [{"name": metric, "version": int(meta["version"]),
                             "status": meta["status"]}] if metric else [],
                "cutoff": None if path in ("clarify", "refused") else "latest",
                "data_as_of": None if path in ("clarify", "refused") else load.isoformat(),
                "latency_ms": max(10, int(noise.gauss(cost[0], cost[0] * 0.2))),
                "tokens": {"prompt": int(cost[1] * 0.8), "completion": int(cost[1] * 0.2)},
                "model": today_model if path in ("governed", "sql_fallback") else "router",
                "answer_fingerprint": hashlib.sha256(said.encode()).hexdigest()[:12],
                "feedback": "down" if roll < feedback_down else "up" if roll > 0.7 else None})
    return records


def write(records, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    for record in records:
        log.write(record, path)                # every record is checked against the schema
    return path


def as_json(records):
    return json.dumps(records, indent=1)
