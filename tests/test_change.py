"""Change management (guide ch 9): classify manifest changes, find their impact, gate."""

import copy

import pytest

from change import demo, impact, manifest_diff
from serving import layer

BASE = layer.manifest()


def changed(edit):
    head = copy.deepcopy(BASE)
    edit(head)
    return {(c["metric"], c["kind"]): c["breaking"] for c in manifest_diff.diff(BASE, head)}


def metric(manifest, name):
    return next(m for m in manifest["metrics"] if m["name"] == name)


def aggregation(manifest, name):
    """A simple metric's own aggregation (dbt's latest spec has no measures)."""
    return metric(manifest, name)["type_params"]["metric_aggregation_params"]


def model(manifest, name):
    return next(m for m in manifest["semantic_models"] if m["name"] == name)


def test_no_change_is_no_change():
    assert manifest_diff.diff(BASE, copy.deepcopy(BASE)) == []


def test_a_simple_metric_formula_change_breaks_every_metric_built_on_it():
    found = changed(lambda m: metric(m, "contribution_before_ads_v1")["type_params"].update(
        expr="contribution_before_ads_approved_rule"))
    assert found == {("contribution_before_ads_v1", "formula"): True,
                     ("contribution_after_ads", "formula"): True}


def test_a_filter_is_breaking():
    found = changed(lambda m: metric(m, "goods_value").update(
        filter={"where_filters": [{"where_sql_template": "{{ Entity('channel') }} = 'email'"}]}))
    assert found[("goods_value", "filter")] is True


def test_removing_a_dimension_changes_the_grain_but_adding_one_does_not():
    def remove(m):
        model(m, "order_lines")["dimensions"] = [d for d in model(m, "order_lines")["dimensions"]
                                                 if d["name"] != "product"]
    assert changed(remove)[("goods_value", "grain")] is True

    def add(m):
        model(m, "order_lines")["dimensions"].append(
            {"name": "quantity_band", "type": "categorical", "expr": "quantity"})
    assert changed(add) == {}


def test_a_rename_is_recognised_and_breaking():
    found = changed(lambda m: metric(m, "cash_received").update(name="cash_received_net"))
    assert found == {("cash_received", "rename"): True}


def test_added_removed_and_metadata_only():
    def edit(m):
        extra = copy.deepcopy(metric(m, "order_count"))
        extra["name"] = "order_count_v2"
        extra["config"]["meta"]["status"] = "draft"
        extra["type_params"]["expr"] = "case when promotion_count > 0 then order_id end"
        m["metrics"].append(extra)
        m["metrics"].remove(metric(m, "refund_rate"))
        metric(m, "advertising")["description"] = "Shorter."
    found = changed(edit)
    assert found == {("order_count_v2", "added"): False, ("refund_rate", "removed"): False,
                     ("advertising", "metadata-only"): False}   # refund_rate is a draft


def test_impact_lists_verified_queries_eval_cases_and_consumers():
    found = impact.impact(BASE, ["contribution_before_ads_v1"])
    assert "contribution_after_ads" in found["metrics"]
    assert found["verified_queries"] == ["vq_contribution_friday_review"]
    assert "s01_friday_make" in found["eval_cases"]
    assert any(c.startswith("trading_review_assistant") for c in found["consumers"])


def test_gate_fails_on_breaking_unless_approved():
    head = copy.deepcopy(BASE)
    aggregation(head, "goods_value").update(agg="max")
    assert demo.gate(BASE, head)[0] == 1
    assert demo.gate(BASE, head, allow_breaking=True)[0] == 0
    assert demo.gate(BASE, copy.deepcopy(BASE))[0] == 0


def test_the_production_manifest_matches_the_project():
    """What is committed as production must be what this project builds, until a change."""
    code, lines = demo.gate(demo.base_manifest(), BASE)
    assert code == 0, "\n".join(lines)
    assert lines[0] == "No changes to any metric.", "re-record it with `sl release`:\n" + "\n".join(lines)


@pytest.mark.slow
def test_change_demo_fails_in_place_and_passes_as_v2(capsys):
    assert demo.change_demo() == 0
    out = capsys.readouterr().out
    assert "BREAKING formula" in out and "GATE: FAIL" in out
    assert "FAIL friday_contribution_paid_rule: total expected 50, got 0" in out
    assert "metadata-only  contribution_after_ads " in out and "GATE: PASS" in out
    assert "Deprecated: Notice given on Friday 2 October 2026: from 00:00 on Monday 5 October" in out
    assert "vq_contribution_current (new, v2)" in out


def test_a_change_to_a_definitions_defaults_is_breaking_not_metadata():
    """Guide ch 9's defaults example: same formula, but a question that leaves the
    channel open now gets every channel instead of paid search."""
    found = changed(lambda m: metric(m, "contribution_after_ads")["config"]["meta"]["defaults"]
                    ["filters"].update(channel="email"))
    assert found == {("contribution_after_ads", "selection"): True}
    found = changed(lambda m: metric(m, "cash_received")["config"]["meta"].update(cannot_group_by=[]))
    assert found == {("cash_received", "selection"): True}


def test_every_consumer_is_registered_with_the_books_fields():
    import yaml
    consumers = yaml.safe_load((demo.ROOT / "change" / "consumers.yml").read_text())["consumers"]
    for c in consumers:
        assert c["purpose"] and c["owner"] and c["surface"], c["id"]
        assert c["kind"] in ("exploratory", "operational", "customer-facing"), c["id"]
        assert ("pinned_until" in c and "pin_reason" in c) and bool(c["pinned_until"]) == bool(c["pin_reason"])


def test_release_records_production_and_appends_a_classified_changelog(tmp_path, monkeypatch, capsys):
    import json
    target = tmp_path / "dbt" / "target"
    target.mkdir(parents=True)
    (target / "semantic_manifest.json").write_text(json.dumps(BASE))
    before = copy.deepcopy(BASE)
    aggregation(before, "goods_value").update(agg="max")            # production differs by one formula
    (tmp_path / "production.json").write_text(json.dumps(before))
    monkeypatch.setattr(demo, "ROOT", tmp_path)
    monkeypatch.setattr(demo, "DBT_DIR", tmp_path / "dbt")
    monkeypatch.setattr(demo, "PRODUCTION", tmp_path / "production.json")
    assert demo.release(today="2026-10-05") == 0
    changelog = (tmp_path / "CHANGELOG.md").read_text()
    assert "## 2026-10-05" in changelog and "- BREAKING formula: `goods_value`" in changelog
    assert json.loads((tmp_path / "production.json").read_text()) == BASE
