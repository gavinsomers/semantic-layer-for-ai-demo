"""The response contract (book chapter 4, guide chapter 4).

Every path ends here: a governed answer, a flagged SQL fallback, a refusal or
a clarifying question. Each is the same shape, checked against
response_contract.schema.json, so any consumer (an agent, a BI tool, a test)
can read the value, its filters, its cutoff, its caveats and its lineage.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

SCHEMA = json.loads((Path(__file__).parent / "response_contract.schema.json")
                    .read_text(encoding="utf-8"))


def new(question, user, category, path, **fields):
    """A contract with every field present; pass only what this path fills in."""
    contract = {
        "contract_version": "1.0",
        "question": question or "",
        "user": user,
        "category": category,
        "path": path,
        "answer_text": "",
        "metrics": [],
        "total": None,
        "values": [],
        "filters_applied": [],
        "cutoff": None,
        "time_window": None,
        "caveats": [],
        "lineage": [],
        "compiled_sql": None,
        "clarification": None,
        "refusal_reason": None,
        "access": None,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    unknown = set(fields) - set(contract)
    if unknown:
        raise KeyError(f"Not contract fields: {', '.join(sorted(unknown))}")
    contract.update(fields)
    return contract


def refused(question, user, reason, category="unsupported", **fields):
    return new(question, user, category, "refused", refusal_reason=reason,
               answer_text=f"I can't answer that. {reason[0].upper() + reason[1:]}", **fields)


def clarify(question, user, clarifying_question, options, category="constrained"):
    return new(question, user, category, "clarify", answer_text=clarifying_question,
               clarification={"question": clarifying_question, "options": list(options)})


def validate(contract):
    """Raise jsonschema.ValidationError if the contract breaks the schema."""
    jsonschema.validate(contract, SCHEMA)
    return contract


def lineage_entry(metric):
    """One lineage line from a metric's operating-agreement metadata."""
    meta = metric["meta"]
    return {"metric": metric["name"], "label": metric["label"],
            "version": int(meta["version"]), "owner": meta["owner"],
            "approver": meta["approver"], "status": meta["status"],
            "last_reviewed": str(meta["last_reviewed"])}
