"""The model loop (ch 4): running out of turns is reported, never passed off as an answer."""

import json
from types import SimpleNamespace as NS

from serving import llm, wrapper


class LoopingClient:
    """A fake model server whose model calls a tool on every turn and never answers."""

    def __init__(self):
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **_):
        function = {"name": "run_sql", "arguments": '{"sql": "select 1"}'}
        call = NS(id="1", function=NS(**function),
                  model_dump=lambda: {"id": "1", "type": "function", "function": function})
        return NS(usage=NS(prompt_tokens=1, completion_tokens=1),
                  choices=[NS(message=NS(content="", tool_calls=[call]))])


def test_running_out_of_turns_returns_no_answer(monkeypatch):
    monkeypatch.setattr(llm, "connect", lambda: (LoopingClient(), "fake-model"))
    stats = {}
    text, calls = llm.chat_with_tools("system", "question", [], lambda name, args: {"rows": []},
                                      max_turns=3, stats=stats)
    assert text is None and len(calls) == 3 and stats["no_answer"] is True


def test_the_wrapper_prints_and_saves_no_answer(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(llm, "connect", lambda: (LoopingClient(), "fake-model"))
    monkeypatch.setattr(llm, "describe", lambda: "fake-model")
    monkeypatch.setattr(wrapper, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(wrapper, "ROOT", tmp_path.parent)
    wrapper.run("What did we make on paid search in the Friday review?", runs=1)
    assert "(no answer after 8 turns)" in capsys.readouterr().out
    saved = json.loads(next(tmp_path.glob("wrapper-*.json")).read_text())
    assert saved["runs"][0]["answer"] == "(no answer after 8 turns)"
