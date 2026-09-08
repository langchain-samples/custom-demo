"""Deterministic tests for the ask_user HITL tool (no graph, no model).

`interrupt()` is monkeypatched on the module UNDER TEST (that is where the name is
looked up, now that it is a top-level import) to stand in for the client's resume value, so we
exercise the real tool body: the interrupt PAYLOAD (the `kind` the frontend
switches on + the question and its multiple-choice options) and the ANSWER
parsing (dict / wrapped / raw).
"""

from custom_demo.runtime.tools import simulated
from custom_demo.runtime.tools.simulated import ask_user


def test_interrupt_payload_is_user_question_with_options(monkeypatch):
    captured: dict = {}

    def fake(payload):
        captured.update(payload)
        return {"answer": "ok"}

    monkeypatch.setattr(simulated, "interrupt", fake)
    ask_user.invoke({"question": "Which range?", "options": ["Last month", "Last quarter"]})
    assert captured["kind"] == "user_question"  # the frontend card discriminant
    assert captured["question"] == "Which range?"
    # The card renders one radio per option, so blanks would be unclickable rows.
    assert captured["options"] == ["Last month", "Last quarter"]


def test_options_are_trimmed_and_blanks_dropped(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(simulated, "interrupt", lambda p: captured.update(p) or {"answer": "a"})
    ask_user.invoke({"question": "q", "options": [" TVs ", "", "   ", "Laptops"]})
    assert captured["options"] == ["TVs", "Laptops"]


def test_returns_answer_from_dict(monkeypatch):
    monkeypatch.setattr(simulated, "interrupt", lambda _p: {"answer": "next quarter"})
    assert ask_user.invoke({"question": "which range?", "options": ["a", "b"]}) == "next quarter"


def test_returns_answer_from_wrapped_draft(monkeypatch):
    monkeypatch.setattr(simulated, "interrupt", lambda _p: {"draft": {"answer": "TVs"}})
    assert ask_user.invoke({"question": "which product?", "options": ["TVs"]}) == "TVs"


def test_returns_raw_string_answer(monkeypatch):
    monkeypatch.setattr(simulated, "interrupt", lambda _p: "just text")
    assert ask_user.invoke({"question": "q", "options": ["a", "b"]}) == "just text"


def test_empty_answer_is_empty_string(monkeypatch):
    monkeypatch.setattr(simulated, "interrupt", lambda _p: None)
    assert ask_user.invoke({"question": "q", "options": ["a", "b"]}) == ""
