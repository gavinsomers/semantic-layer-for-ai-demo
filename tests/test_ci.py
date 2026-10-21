"""CI wiring (guide ch 6) and templates (Appendix C) stay in step with the code."""

import json
import re
import shutil

import pytest
import yaml

import sl

ROOT = sl.ROOT


def test_workflow_runs_the_same_steps_as_sl_ci_in_the_same_order():
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    names = [s.get("name") for s in workflow["jobs"]["semantic-layer"]["steps"] if s.get("name")]
    steps = [n for n in names if n not in ("uv sync",)]
    assert steps == [name for name, _ in sl.CI_STEPS]
    runs = " ".join(str(s.get("run", "")) for s in workflow["jobs"]["semantic-layer"]["steps"])
    for _, arguments in sl.CI_STEPS:
        assert "python -m sl " + " ".join(arguments) in runs


def test_every_check_after_build_reports_and_labels_re_run_the_gate():
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    steps = workflow["jobs"]["semantic-layer"]["steps"]
    names = [s.get("name") for s in steps]
    for step in steps[names.index("build") + 1:]:
        assert step.get("if") == "${{ !cancelled() }}", step["name"]
    triggers = workflow[True]["pull_request"]["types"]       # YAML 1.1 reads `on:` as True
    assert {"opened", "synchronize", "reopened", "labeled", "unlabeled"} <= set(triggers)


def test_codeowners_cover_metric_files_lockfile_and_context():
    owners = (ROOT / ".github" / "CODEOWNERS").read_text()
    for path in ("/dbt/models/semantic/", "/context/", "/authoring/reviewed.lock.json",
                 "/ci/expected_metrics.yml", "/evals/cases.yml", "/tests/test_friday_figures.py",
                 "/tests/test_metric_regression.py"):
        assert re.search(rf"^{re.escape(path)}\s+@", owners, re.MULTILINE), path


def test_pull_request_template_asks_for_business_sign_off():
    template = (ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md").read_text()
    assert "## Business sign-off" in template and "BREAKING" in template


def test_templates_parse_and_copies_match_the_files_in_use():
    for name in ("metric_metadata.yml", "verified_query.yml", "eval_case.yml"):
        assert yaml.safe_load((ROOT / "templates" / name).read_text())
    assert (ROOT / "templates" / "pull_request_template.md").read_text() == \
        (ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md").read_text()
    assert json.loads((ROOT / "templates" / "telemetry_log.schema.json").read_text()) == \
        json.loads((ROOT / "telemetry" / "log_schema.json").read_text())


def test_gitignore_covers_generated_files():
    ignored = (ROOT / ".gitignore").read_text().split()
    for entry in ("logs/", "__pycache__/", "dbt/target/", "warehouse.duckdb", ".venv/",
                  "evals/scratch/", "authoring/scratch/", "telemetry/out/"):
        assert entry in ignored, entry


def test_no_em_dashes_in_prose():
    for pattern in ("*.md", "*/*.md", "*/*.yml", "*/*.py", "*.py"):
        for path in ROOT.glob(pattern):
            if ".venv" in path.parts or "results" in path.parts:
                continue
            assert "\u2014" not in path.read_text(encoding="utf-8"), path


@pytest.mark.slow
def test_validate_parses_first_and_fails_on_a_warning_not_allowed(tmp_path, capsys):
    """sl validate runs dbt parse on the YAML as it is now, then fails on any
    warning missing from ALLOWED_WARNINGS (here: a test written the deprecated way)."""
    scratch = tmp_path / "dbt"
    shutil.copytree(sl.DBT_DIR, scratch, ignore=shutil.ignore_patterns("target", "logs"))
    shutil.copy(ROOT / "warehouse.duckdb", tmp_path / "warehouse.duckdb")
    semantic = scratch / "models" / "semantic" / "semantic_layer.yml"
    text = semantic.read_text()
    old = "          - accepted_values:\n              arguments:\n                values: [in, out]"
    assert old in text
    semantic.write_text(text.replace(old, "          - accepted_values:\n              values: [in, out]"))
    assert sl.validate(None, project=scratch) == 1
    assert "warning(s) not in ALLOWED_WARNINGS" in capsys.readouterr().out
    semantic.write_text(text)
    assert sl.validate(None, project=scratch) == 0


def test_the_code_and_templates_are_mit_licensed():
    licence = (ROOT / "LICENSE").read_text()
    assert licence.startswith("MIT License") and "Copyright (c) 2026 Gavin Somers" in licence
    readme = (ROOT / "README.md").read_text()
    assert "templates/" in readme and "CC BY-NC-ND 4.0" in readme
