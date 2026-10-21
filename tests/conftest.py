"""Shared test set-up. Run with `uv run python -m sl test`, which builds the
warehouse first. No model server is needed: the agent runs in offline mode."""

import pytest

from serving import access, layer
from telemetry import log


def pytest_sessionstart(session):
    if not layer.WAREHOUSE.exists() or not layer.MANIFEST.exists():
        pytest.exit("No warehouse yet. Run `uv run python -m sl build` first.", returncode=2)


@pytest.fixture(autouse=True)
def audit_log_in_tmp(tmp_path, monkeypatch):
    """Each test writes its own audit and telemetry logs, so tests can read them back."""
    path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(access, "AUDIT_LOG", path)
    monkeypatch.setattr(log, "TELEMETRY_LOG", tmp_path / "telemetry.jsonl")
    return path
