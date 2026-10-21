"""Access control and the audit log (guide chapter 7).

The access check runs in trusted code, from the caller's identity, before
anything is calculated. Every tool call is written to logs/audit.jsonl: who
asked, what, which path answered, which metric and version, whether access
refused it, and a request_id shared by every line written for one question.
"""

import contextvars
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
USERS_FILE = Path(__file__).resolve().parent / "users.yml"
AUDIT_LOG = ROOT / "logs" / "audit.jsonl"


class AccessDenied(Exception):
    """The caller may not have this. The message never reveals hidden values."""


def load():
    return yaml.safe_load(USERS_FILE.read_text(encoding="utf-8"))


def user(name):
    """The policy for one user. Unknown users get nothing."""
    users = load()["users"]
    if name not in users:
        raise AccessDenied(f"Unknown user '{name}'. Known users: {', '.join(users)}.")
    return {"name": name, **users[name]}


def check_metric(policy, metric):
    """May this user use this metric at all? Checked before anything runs."""
    if metric in policy.get("deny_metrics", []):
        raise AccessDenied(f"the {policy['name'].replace('_', ' ')} is not permitted to use {metric}.")


def row_filters(policy):
    """Row filters this user always carries, e.g. {"product_group": "lighting"}."""
    return dict(policy.get("row_filters") or {})


def sql_policy(policy):
    """Tables, and the allowed columns of each, this user may read on the SQL fallback.
    A user's list can only narrow sql_columns, never add to it."""
    catalogue = load()["sql_columns"]
    return {"tables": {t: [c for c in columns if c in catalogue.get(t, [])]
                       for t, columns in (policy.get("sql_tables") or {}).items() if t in catalogue},
            "row_filters": row_filters(policy)}


# The question being answered: its request_id and, where known, the model.
# A context variable, so threads answering different questions never mix them.
REQUEST = contextvars.ContextVar("request", default=None)


def start_request(model=None):
    """Begin one question; every audit line until the next one shares its request_id.
    The contract's schema is closed, so the id lives here and in telemetry."""
    request = {"request_id": uuid.uuid4().hex[:12], **({"model": model} if model else {})}
    REQUEST.set(request)
    return request


def audit(**record):
    """Append one line to logs/audit.jsonl. Never log secrets or result values."""
    AUDIT_LOG.parent.mkdir(exist_ok=True)
    request = REQUEST.get() or {"request_id": uuid.uuid4().hex[:12]}   # a lone tool call
    from serving import clock
    record = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **request,
              "as_of": clock.today().isoformat(), "access_denied": False, **record}
    with AUDIT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")
