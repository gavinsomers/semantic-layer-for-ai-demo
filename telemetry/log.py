"""One telemetry record per answer (guide chapter 10).

The audit log (serving/access.py, logs/audit.jsonl) records every TOOL call:
who called what, with which filters. This log records every ANSWER, in the
shape of telemetry/log_schema.json, so the weekly report can ask:

    how often did an answer bypass the layer?    path
    why was a question refused?                  reason_code
    which metrics, at which versions, answered?  metrics
    did the same question get a different answer? answer_fingerprint
    how fresh was the data, how slow, how costly? data_as_of, latency_ms, tokens
    was the person satisfied?                    feedback

It never stores result values: a fingerprint (a short hash of the values)
is enough to see that an answer changed, without copying the figure into a log.
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parent.parent
TELEMETRY_LOG = ROOT / "logs" / "telemetry.jsonl"
SCHEMA = json.loads((Path(__file__).parent / "log_schema.json").read_text(encoding="utf-8"))


REASON_CODES = ["no_metric", "access", "unsupported_breakdown", "unsupported_question",
                "unknown_cost", "fallback_blocked", "sql_rejected", "qualifier", "on_hold", "other"]


def reason_code(result):
    """Why an answer was refused, as a code the report can count; None if not refused.
    Read from the contract the code wrote, so every path gets the same code."""
    if result["path"] != "refused":
        return None
    reason = (result.get("refusal_reason") or "").lower()
    if "call query_metrics first" in reason or "cannot widen access" in reason:
        return "fallback_blocked"
    if (result.get("access") or {}).get("denied"):
        return "access"
    if "not a governed metric" in reason or "no governed metric matches" in reason:
        return "no_metric"                        # a coverage gap: a candidate metric
    if "asks only for" in reason:
        return "qualifier"                        # a narrowing no metric or filter applies
    if "is on hold until" in reason:
        return "on_hold"
    if "cannot be split by" in reason or "cannot be broken down or filtered by" in reason:
        return "unsupported_breakdown"
    if "unknown cost" in reason:
        return "unknown_cost"
    if result.get("access") is not None and not result.get("metrics"):
        return "sql_rejected"                     # refused by the SQL guard
    return "unsupported_question" if result["category"] == "unsupported" else "other"


def fingerprint(result, reason=None):
    """A 12-character hash of what the answer said (values, total, path, refusal
    reason code), not the values."""
    said = {"path": result["path"], "total": result.get("total"),
            "values": result.get("values") or [],
            "options": (result.get("clarification") or {}).get("options"),
            "reason_code": reason or reason_code(result)}
    return hashlib.sha256(json.dumps(said, sort_keys=True, default=str).encode()).hexdigest()[:12]


def record_for(result, latency_ms, stats=None, ts=None, request_id=None, reason=None):
    """The telemetry record for one response contract. request_id is the audit
    log's, so an answer can be traced to its tool calls; `reason` overrides the
    reason code (the MCP server's blocked fallback is "fallback_blocked")."""
    stats = stats or {}
    reason = reason or reason_code(result)
    return {
        "ts": ts or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "answer_id": request_id or uuid.uuid4().hex[:12],
        "user": result["user"],
        "question": result["question"],
        "category": result["category"],
        "path": result["path"],
        "metrics": [{"name": line["metric"], "version": line["version"], "status": line["status"]}
                    for line in result.get("lineage") or []],
        "cutoff": (result.get("cutoff") or {}).get("id"),
        "data_as_of": (result.get("cutoff") or {}).get("at"),
        "latency_ms": int(latency_ms),
        "tokens": {"prompt": stats.get("prompt_tokens", 0),
                   "completion": stats.get("completion_tokens", 0)},
        "model": stats.get("model", "offline"),
        "reason_code": reason,
        # Metrics whose figure carried an unusual-value caveat (serving/unusual.py),
        # so the report can count them without reading caveat text.
        "unusual": [line["metric"] for line in result.get("lineage") or []
                    if any(str(c).startswith("Unusual value:") for c in result.get("caveats") or [])],
        "answer_fingerprint": fingerprint(result, reason),
        "feedback": None,
    }


def write(record, path=None):
    """Check a record against the schema and append it to the log."""
    jsonschema.validate(record, SCHEMA)
    path = Path(path or TELEMETRY_LOG)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")
    return record


def read(path=None):
    path = Path(path or TELEMETRY_LOG)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
