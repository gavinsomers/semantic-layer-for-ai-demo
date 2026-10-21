"""The response contract and its JSON Schema (ch 4)."""

import jsonschema
import pytest

from serving import agent, contract, tools


def test_every_path_returns_a_valid_contract():
    paths = {}
    for question in ["What did we make on paid search in the Friday review?",
                     "What was our profit last week?",
                     "Why did lighting fall?",
                     "How many orders used more than one promotion?"]:
        result, _ = agent.ask(question, "finance_lead", offline=True)
        contract.validate(result)
        paths[result["path"]] = result
    assert set(paths) == {"governed", "clarify", "refused", "sql_fallback"}
    governed = paths["governed"]
    assert governed["compiled_sql"].upper().startswith(("SELECT", "WITH"))
    assert governed["lineage"][0]["metric"] == "contribution_after_ads"
    assert paths["sql_fallback"]["lineage"] == []
    assert "Not a governed definition" in paths["sql_fallback"]["caveats"][0]
    assert paths["refused"]["compiled_sql"] is None


def test_schema_rejects_a_governed_answer_without_lineage():
    good = tools.query_metrics("finance_lead", "goods_value", cutoff="friday_review")
    bad = dict(good, lineage=[])
    with pytest.raises(jsonschema.ValidationError):
        contract.validate(bad)


def test_schema_rejects_a_refusal_carrying_values():
    bad = contract.refused("q", "finance_lead", "no")
    bad["values"] = [{"x": 1}]
    with pytest.raises(jsonschema.ValidationError):
        contract.validate(bad)


def test_schema_rejects_an_unknown_path():
    bad = contract.new("q", "finance_lead", "supported", "guess")
    with pytest.raises(jsonschema.ValidationError):
        contract.validate(bad)


def test_model_wording_with_an_invented_figure_is_withheld():
    governed = tools.query_metrics("finance_lead", "contribution_after_ads", cutoff="friday_review")
    result = agent.finish("q", "finance_lead", "Paid search made £50 from £1,500 of goods.", [governed])
    assert "£1,500" not in result["answer_text"]
    assert any("withheld" in note for note in result["caveats"])
    ok = agent.finish("q", "finance_lead", "Contribution was £50.", [governed])
    assert ok["answer_text"] == "Contribution was £50."


def test_show_names_the_window_after_the_metrics_time_dimension(capsys):
    import sl
    for metric, line in (("stock_available", "Snapshot dates: 2026-09-01 to 2026-09-04"),
                         ("goods_value", "Orders accepted: 2026-09-01 to 2026-09-04")):
        sl.show(contract.new("q", "finance_lead", "supported", "governed", metrics=[metric],
                             time_window={"start": "2026-09-01", "end": "2026-09-04"}))
        assert line in capsys.readouterr().out
