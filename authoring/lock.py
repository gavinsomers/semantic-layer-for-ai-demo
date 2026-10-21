"""Block unreviewed edits to the reviewed files with a hash check (guide chapter 5).

    uv run python -m sl lock --reviewer "head of data"    after a review
    uv run python -m sl check-lock                         in CI: fails on any unreviewed change

An agent (or a person in a hurry) can edit a metric file and the change may
still parse, validate and pass every test. The lockfile records the SHA-256
of each reviewed file and who reviewed it. CI recomputes the hashes: a file
that changed, appeared or vanished since its review fails the build until a
reviewer runs `sl lock`. CODEOWNERS makes the lockfile itself need an owner's
approval, so the agent cannot re-lock its own work.
"""

import hashlib
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCKFILE = "authoring/reviewed.lock.json"
# Everything a model reads as meaning: the semantic YAML and the context files.
PATTERNS = ["dbt/models/semantic/*.yml", "context/*.yml", "context/*.md"]


def digest(path):
    """SHA-256 of a text file with line endings normalised, so a Windows
    checkout (CRLF) hashes the same as Linux."""
    text = Path(path).read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(text).hexdigest()


def current(root=ROOT):
    return {str(p.relative_to(root)).replace("\\", "/"): digest(p)
            for pattern in PATTERNS for p in sorted(root.glob(pattern))}


def read(root=ROOT):
    path = root / LOCKFILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"files": {}}


def write(reviewer, root=ROOT, today=None):
    """Record the current hashes. Files whose hash is unchanged keep their old reviewer."""
    old = read(root)["files"]
    files = {}
    for name, sha in current(root).items():
        kept = old.get(name, {})
        files[name] = kept if kept.get("sha256") == sha else {
            "sha256": sha, "reviewed_by": reviewer,
            "reviewed_at": today or date.today().isoformat()}
    (root / LOCKFILE).write_text(json.dumps({"files": files}, indent=2, sort_keys=True) + "\n",
                                 encoding="utf-8")
    return files


def check(root=ROOT):
    """A list of problems: empty means every reviewed file is as reviewed."""
    locked, now = read(root)["files"], current(root)
    problems = [f"changed since review by {locked[n]['reviewed_by']} on "
                f"{locked[n]['reviewed_at']}: {n}"
                for n in now if n in locked and locked[n]["sha256"] != now[n]]
    problems += [f"never reviewed: {n}" for n in now if n not in locked]
    problems += [f"reviewed but missing: {n}" for n in locked if n not in now]
    return problems
