"""AI-drafted models (guide ch 5): the gates, the critic checklist, the lockfile."""

import shutil

import pytest
import yaml

from authoring import draft, lock

ROOT = lock.ROOT


def canned(number):
    return yaml.safe_load((draft.HERE / "canned" / f"refunds_round{number}.yml").read_text())


def metrics(d):
    """The draft's metrics: on the mart's models entry (dbt's latest spec)."""
    return d["models"][0]["metrics"]


def test_checklist_catches_the_round_1_draft_and_passes_round_2():
    issues = draft.checklist(canned(1))
    assert any("no cutoff entity" in i for i in issues)
    assert any("snapshot_key" in i for i in issues)
    assert draft.checklist(canned(2)) == []


def test_checklist_wants_meta_and_a_draft_status():
    bad = canned(2)
    metrics(bad)[0]["config"]["meta"].pop("owner")
    metrics(bad)[1]["config"]["meta"]["status"] = "certified"
    issues = draft.checklist(bad)
    assert any("lacks owner" in i for i in issues)
    assert any("must be status: draft" in i for i in issues)


def test_offline_draft_fails_a_gate_then_passes_and_waits_for_a_person(tmp_path):
    result = draft.run(live=False, out=tmp_path / "refunds.yml")
    first, second = result["rounds"]
    assert [g["gate"] for g in first["gates"]] == ["parse", "questions"]
    assert first["gates"][0]["passed"] and not first["gates"][1]["passed"]
    assert "without one it answers 450, not 150" in first["gates"][1]["detail"]
    assert all(g["passed"] for g in second["gates"]) and len(second["gates"]) == 3
    assert result["ready_for_review"]
    assert not (ROOT / "dbt" / "models" / "semantic" / "refunds.yml").exists()   # not merged


def copy_reviewed_files(tmp_path):
    for pattern in lock.PATTERNS:
        for path in ROOT.glob(pattern):
            target = tmp_path / path.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(path, target)
    (tmp_path / "authoring").mkdir(exist_ok=True)
    return tmp_path


def test_lock_detects_unreviewed_new_and_missing_files(tmp_path):
    root = copy_reviewed_files(tmp_path)
    lock.write("head of data", root=root, today="2026-10-02")
    assert lock.check(root) == []
    semantic = root / "dbt" / "models" / "semantic" / "semantic_layer.yml"
    semantic.write_text(semantic.read_text().replace("status: certified", "status: draft", 1))
    (root / "context" / "new_rules.md").write_text("A new rule.")
    (root / "context" / "instructions.md").unlink()
    problems = lock.check(root)
    assert any(p.startswith("changed since review by head of data on 2026-10-02") for p in problems)
    assert "never reviewed: context/new_rules.md" in problems
    assert "reviewed but missing: context/instructions.md" in problems


def test_lock_ignores_line_endings_and_keeps_old_reviewers(tmp_path):
    root = copy_reviewed_files(tmp_path)
    lock.write("head of data", root=root, today="2026-10-02")
    path = root / "context" / "synonyms.yml"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))     # a Windows checkout
    assert lock.check(root) == []
    (root / "context" / "ambiguity.yml").write_text("changed: true\n")
    files = lock.write("finance lead", root=root, today="2026-10-03")
    assert files["context/ambiguity.yml"]["reviewed_by"] == "finance lead"
    assert files["context/synonyms.yml"]["reviewed_by"] == "head of data"


def test_approving_a_draft_merges_it_and_relocks(tmp_path):
    root = copy_reviewed_files(tmp_path)
    reviewed = tmp_path / "refunds.yml"
    shutil.copy(draft.HERE / "canned" / "refunds_round2.yml", reviewed)
    target = draft.approve(reviewed, "finance lead", root=root, override=True, today="2026-10-21")
    assert target == root / "dbt" / "models" / "semantic" / "refunds.yml" and target.exists()
    assert not (root / "dbt" / draft.MART_PROPERTIES).exists()    # the draft replaced it
    assert lock.check(root) == []


def test_the_repository_lockfile_is_current():
    assert lock.check() == [], "run `sl lock --reviewer ...` after review"


def test_approval_records_who_approved_it_and_when(tmp_path):
    root = copy_reviewed_files(tmp_path)
    reviewed = tmp_path / "refunds.yml"
    shutil.copy(draft.HERE / "canned" / "refunds_round2.yml", reviewed)
    draft.record_gates(reviewed, [], passed=True)
    target = draft.approve(reviewed, "finance lead", root=root, today="2026-10-21")
    meta = metrics(yaml.safe_load(target.read_text()))[0]["config"]["meta"]
    assert meta["last_reviewed"] == "2026-10-21" and meta["approver"] == "finance lead"


def test_a_draft_that_did_not_pass_its_gates_is_not_merged(tmp_path):
    root = copy_reviewed_files(tmp_path)
    failed = tmp_path / "refunds.yml"
    shutil.copy(draft.HERE / "canned" / "refunds_round1.yml", failed)
    with pytest.raises(draft.NotApproved, match="no recorded gate result"):
        draft.approve(failed, "finance lead", root=root)
    draft.record_gates(failed, [], passed=False)
    with pytest.raises(draft.NotApproved, match="did not pass the gates"):
        draft.approve(failed, "finance lead", root=root)
    draft.record_gates(failed, [], passed=True)
    failed.write_text(failed.read_text() + "# edited after the gates ran\n")
    with pytest.raises(draft.NotApproved, match="changed after the gates ran"):
        draft.approve(failed, "finance lead", root=root)
    assert not (root / "dbt" / "models" / "semantic" / "refunds.yml").exists()


def test_the_checklist_rejects_a_review_date_on_a_draft():
    reviewed = canned(2)
    metrics(reviewed)[0]["config"]["meta"]["last_reviewed"] = "2026-10-02"
    assert any("last_reviewed must be null" in i for i in draft.checklist(reviewed))


# Revision 2 follow-ups: the latest-spec example, named legacy keys, dates.

def test_the_prompt_shows_a_latest_spec_non_additive_dimension():
    prompt = draft.draft_prompt()
    assert "  - name: fct_cash_movements" in prompt and "  - name: fct_stock_daily" in prompt
    assert "window_agg: max" in prompt and "group_by: [product_group]" in prompt
    assert "Refund amounts are additive" in prompt


def legacy_draft():
    """Round 2 with the legacy non_additive_dimension keys a live drafter wrote."""
    bad = canned(2)
    metrics(bad)[0]["non_additive_dimension"] = {"name": "refund_accepted_date",
                                                 "window_choice": "max", "window_groupings": []}
    metrics(bad)[1]["type_params"] = {"measure": "amount_approved"}
    return bad


def test_unknown_and_legacy_keys_are_named_for_the_drafter():
    assert draft.unknown_keys(draft._entry(canned(2))) == []
    problems = " ".join(draft.unknown_keys(draft._entry(legacy_draft())))
    assert "'window_choice' (latest spec: window_agg)" in problems
    assert "'window_groupings' (latest spec: group_by)" in problems
    assert "lacks window_agg" in problems
    assert "refunds_approved: unknown key(s) 'type_params'" in problems
    bare = canned(2)
    metrics(bare)[0]["non_additive_dimension"] = "refund_accepted_date"
    assert "must be a mapping" in draft.unknown_keys(draft._entry(bare))[0]


@pytest.mark.slow
def test_the_parse_gate_feeds_back_the_legacy_key_by_name():
    text = yaml.safe_dump(legacy_draft(), sort_keys=False)
    with draft.scratch_project(text) as scratch:
        ok, detail = draft.gate_parse(scratch, text)
    assert not ok and "'window_choice' (latest spec: window_agg)" in detail


def test_the_questions_gate_fails_when_every_question_has_one_date(monkeypatch):
    """Every figure right, but each from one date: the question set itself fails."""
    import contextlib
    spec = yaml.safe_load((draft.HERE / "sample_questions.yml").read_text())
    expected = {tuple(draft.layer.where_clause(k, v) for k, v in q["where"].items()) + (q["metric"],):
                q["expected"] for q in spec["questions"]}

    def fake_query(metrics, group_by=(), where=(), **_):
        if group_by:                                              # one date per question
            return {"rows": [{"metric_time__day": "2026-08-31", metrics[0]: 1}], "total": None}
        return {"rows": [], "total": {metrics[0]: expected[tuple(where) + (metrics[0],)]}}

    monkeypatch.setattr(draft.layer, "query", fake_query)
    monkeypatch.setattr(draft, "_project", lambda scratch: contextlib.nullcontext())
    ok, detail = draft.gate_questions("unused", spec)
    assert not ok and "each figure comes from a single date" in detail


@pytest.mark.slow
def test_a_wrong_non_additive_dimension_fails_the_several_dates_question():
    """It passes the single-date questions; only the September close catches it."""
    wrong = canned(2)
    metrics(wrong)[1]["non_additive_dimension"] = {"name": "refund_accepted_date",
                                                   "window_agg": "max"}
    text = yaml.safe_dump(wrong, sort_keys=False)
    with draft.scratch_project(text) as scratch:
        assert draft.gate_parse(scratch, text)[0]
        ok, detail = draft.gate_questions(scratch)
    assert not ok
    assert detail == "'Refunds approved by the September close, all channels': expected 245, got 55"
