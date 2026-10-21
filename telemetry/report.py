"""The weekly telemetry report and its alerts (guide chapter 10).

    uv run python -m sl telemetry-report [logs/telemetry.jsonl]

Reads telemetry records (telemetry/log_schema.json) and answers the questions
a weekly log audit asks:

    bypass rate      share of answers that did not use the layer (sql_fallback)
    unused metrics   governed metrics no answer used
    fallback clusters  fallback questions, and questions refused because no
                     metric covers them (reason_code no_metric), grouped by
                     their words: candidates for new metrics (no embeddings:
                     word overlap is enough)
    drift            the same question answered differently over time, and
                     whether a metric version, model or data change explains it
    freshness        how old the newest data behind current answers was
    feedback         thumbs down by path

ALERTS lists the thresholds that should page someone. They are starting
points for a new deployment, not industry standards: tune them on your
own traffic.
"""

import re
from collections import Counter, defaultdict
from datetime import datetime

from serving import layer

ALERTS = {
    "bypass_rate": 0.15,          # more than 15% of answers bypassed the layer
    "thumbs_down_rate": 0.20,     # on any path
    "freshness_hours": 30,        # the newest data behind current answers is older than this
    "unexplained_drift": 0,       # any answer change with no version, model or data change
}
STOP = set("a an and the of in on for to by did do does we our us was were is are what how many "
           "much which with had have get got last week september august month day each per "
           "more than one two used uptake applied came code".split())
# A few words of normalisation do the work of embeddings at this scale.
CANON = {"promo": "promotion", "promoted": "promotion", "discount": "promotion",
         "purchase": "order", "purchas": "order", "basket": "order", "back": "repeat",
         "second": "repeat"}    # "purchas": normalise() folds "purchases" so


# What can explain a changed answer: (cause, the record field that shows it).
CAUSES = [("a metric version change", "metrics"), ("a model change", "model"),
          ("a data change", "data_as_of"), ("a data change", "cutoff")]


def words(question):
    """The question's words, folded with the router's own normalise() (one
    stemming rule for routing and reporting), stop words and digits out."""
    from serving.router import normalise
    stems = {re.sub(r"[^a-z]", "", t) for t in normalise(question).split()}
    return {CANON.get(t, t) for t in stems if t not in STOP} - {""}


def clusters(questions, threshold=0.34):
    """Greedy word-overlap clustering: [(count, top words, example), ...], largest first."""
    groups = []                                   # [{"words": set, "questions": Counter}]
    for question, count in Counter(questions).most_common():
        mine = words(question)
        best = max(groups, key=lambda g: _jaccard(mine, g["words"]), default=None)
        if best and _jaccard(mine, best["words"]) >= threshold:
            best["questions"][question] += count
            best["words"] |= mine
        else:
            groups.append({"words": set(mine), "questions": Counter({question: count})})
    result = []
    for g in groups:
        tally = Counter(w for q, n in g["questions"].items() for w in words(q) for _ in range(n))
        top = sorted(tally, key=lambda w: (-tally[w], w))[:4]     # ties alphabetical: stable output
        result.append((sum(g["questions"].values()), top,
                       g["questions"].most_common(1)[0][0], len(g["questions"])))
    return sorted(result, reverse=True)


def _jaccard(a, b):
    return len(a & b) / len(a | b) if a | b else 0.0


def analyse(records, exclude_metrics=()):
    answered = [r for r in records if r["path"] in ("governed", "sql_fallback")]
    fallback = [r for r in answered if r["path"] == "sql_fallback"]
    by_day = defaultdict(lambda: [0, 0])
    for r in answered:
        day = r["ts"][:10]
        by_day[day][0] += r["path"] == "sql_fallback"
        by_day[day][1] += 1

    visible = {name for name, m in layer.metrics().items()
               if not m["meta"].get("hidden") and name not in exclude_metrics}
    used = {m["name"] for r in records for m in r["metrics"]}

    # Drift: one question, several fingerprints over time. Each change is
    # explained by whatever differs between the two answers: the metric
    # version, the model, or the data (a new load or another cutoff).
    drift = []
    by_question = defaultdict(list)
    for r in sorted(answered, key=lambda r: r["ts"]):
        by_question[(r["question"], r["user"])].append(r)
    for (question, user), rows in by_question.items():
        pairs = [(a, b) for a, b in zip(rows, rows[1:])
                 if a["answer_fingerprint"] != b["answer_fingerprint"]]
        if not pairs:
            continue
        causes, unexplained = Counter(), 0
        for a, b in pairs:
            found = list(dict.fromkeys(cause for cause, key in CAUSES if a.get(key) != b.get(key)))
            causes.update(found)
            unexplained += not found
        named = " and ".join(c for c, _ in causes.most_common()) or "no version, model or data change"
        drift.append({"question": question, "user": user,
                      "answers": len({r["answer_fingerprint"] for r in rows}),
                      "changes": len(pairs), "unexplained": unexplained,
                      "explained_by": ("partly explained by " if causes and unexplained else
                                       "explained by ") + named})

    lags = defaultdict(float)
    for r in answered:
        if r.get("cutoff") == "latest" and r.get("data_as_of"):
            asked = datetime.fromisoformat(r["ts"]).replace(tzinfo=None)
            lag = (asked - datetime.fromisoformat(r["data_as_of"])).total_seconds() / 3600
            lags[r["ts"][:10]] = max(lags[r["ts"][:10]], lag)

    feedback = {}
    for path in ("governed", "sql_fallback", "clarify", "refused"):
        mine = [r for r in records if r["path"] == path]
        down = sum(r["feedback"] == "down" for r in mine)
        feedback[path] = round(down / len(mine), 3) if mine else 0.0

    model_changes = []
    for a, b in zip(sorted(answered, key=lambda r: r["ts"]), sorted(answered, key=lambda r: r["ts"])[1:]):
        if a["model"] != b["model"]:
            model_changes.append(f"{b['ts'][:16]}: {a['model']} -> {b['model']}")

    tokens = defaultdict(int)
    for r in records:
        tokens[r["path"]] += r["tokens"]["prompt"] + r["tokens"]["completion"]

    # Coverage gaps: fallback questions, and questions refused for want of a metric.
    gaps = fallback + [r for r in records if r.get("reason_code") == "no_metric"]
    return {
        "answers": len(records),
        "reasons": dict(Counter(r.get("reason_code") for r in records if r.get("reason_code"))),
        "paths": dict(Counter(r["path"] for r in records)),
        "bypass_rate": round(len(fallback) / len(answered), 3) if answered else 0.0,
        "bypass_by_day": {d: round(f / n, 3) for d, (f, n) in sorted(by_day.items())},
        "unused_metrics": sorted(visible - used),
        "fallback_clusters": clusters([r["question"] for r in gaps]),
        "drift": sorted(drift, key=lambda d: -d["changes"]),
        "freshness_hours_by_day": {d: round(h, 1) for d, h in sorted(lags.items())},
        "thumbs_down_rate": feedback,
        "model_changes": model_changes,
        "mean_latency_ms": {p: round(sum(r["latency_ms"] for r in records if r["path"] == p)
                                     / max(1, sum(r["path"] == p for r in records)))
                            for p in ("governed", "sql_fallback")},
        "tokens_by_path": dict(sorted(tokens.items())),
    }


def alerts(found):
    raised = []
    if found["bypass_rate"] > ALERTS["bypass_rate"]:
        raised.append(f"bypass rate {found['bypass_rate']:.0%} is above {ALERTS['bypass_rate']:.0%}: "
                      "promote the largest fallback cluster")
    for path, rate in found["thumbs_down_rate"].items():
        if rate > ALERTS["thumbs_down_rate"]:
            raised.append(f"{rate:.1%} thumbs down on {path} answers (above "
                          f"{ALERTS['thumbs_down_rate']:.0%}): turn them into eval cases")
    stale = {d: h for d, h in found["freshness_hours_by_day"].items() if h > ALERTS["freshness_hours"]}
    for day, hours in stale.items():
        raised.append(f"{day}: current answers used data {hours:.0f} hours old "
                      f"(above {ALERTS['freshness_hours']}): check the nightly load")
    unexplained = sum(d["unexplained"] for d in found["drift"])
    if unexplained > ALERTS["unexplained_drift"]:
        raised.append(f"{unexplained} answer change(s) with no metric version, model or data "
                      "change behind them: pin those questions with verified queries")
    for change in found["model_changes"]:
        raised.append(f"model changed at {change}: re-run the full evaluation (sl evals)")
    return raised


def bar(share, width=40):
    return "#" * round(share * width) + "." * (width - round(share * width))


def render(found, title="Telemetry report"):
    lines = [title, "=" * len(title),
             f"{found['answers']} answers: " + ", ".join(f"{k} {v}" for k, v in sorted(found["paths"].items())),
             f"Bypass rate (answers via SQL fallback): {found['bypass_rate']:.1%}", ""]
    for day, share in found["bypass_by_day"].items():
        lines.append(f"  {day}  {bar(share)} {share:.0%}")
    if found["reasons"]:
        lines.append("Refusals by reason: " + ", ".join(f"{k} {v}" for k, v in sorted(found["reasons"].items())))
    lines += ["", "Fallback and no-metric clusters (candidates for new metrics):"]
    for count, top, example, variants in found["fallback_clusters"]:
        lines.append(f"  {count:4} answers, {variants} phrasings  [{', '.join(top)}]  e.g. \"{example}\"")
    lines += ["", "Unused governed metrics: " + (", ".join(found["unused_metrics"]) or "none")]
    lines += ["", "Drift (same question, different answers):"]
    for d in found["drift"][:6]:
        lines.append(f"  {d['changes']} change(s), {d['explained_by']}"
                     f" ({d['unexplained']} unexplained): {d['user']}: {d['question']}")
    if not found["drift"]:
        lines.append("  none")
    lines += ["", "Freshness, newest data behind current answers (hours): "
              + ", ".join(f"{d[5:]} {h}" for d, h in found["freshness_hours_by_day"].items())]
    lines += ["Thumbs down by path: " + ", ".join(f"{p} {r:.0%}" for p, r in found["thumbs_down_rate"].items())]
    lines += [f"Mean latency: governed {found['mean_latency_ms']['governed']} ms, "
              f"SQL fallback {found['mean_latency_ms']['sql_fallback']} ms"]
    lines += ["Tokens by path: " + ", ".join(f"{p} {n:,}" for p, n in found["tokens_by_path"].items()),
              "", "Alerts:"]
    lines += [f"  ALERT {a}" for a in alerts(found)] or ["  none"]
    return "\n".join(lines)
