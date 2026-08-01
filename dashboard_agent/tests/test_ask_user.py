"""Deterministic tests for the ask_user HITL tool (no graph, no model).

`interrupt()` is monkeypatched to stand in for the client's resume value, so we
exercise the real tool body: the interrupt PAYLOAD (the `kind` the frontend
switches on + the question) and the ANSWER parsing (dict / wrapped / raw).
"""

import langgraph.types as lt

from dashboard_agent.tools.simulated import ask_user


def test_interrupt_payload_is_user_question(monkeypatch):
    captured: dict = {}

    def fake(payload):
        captured.update(payload)
        return {"answer": "ok"}

    monkeypatch.setattr(lt, "interrupt", fake)
    ask_user.invoke({"question": "How many months?"})
    assert captured["kind"] == "user_question"  # the frontend card discriminant
    assert captured["question"] == "How many months?"


def test_returns_answer_from_dict(monkeypatch):
    monkeypatch.setattr(lt, "interrupt", lambda _p: {"answer": "next quarter"})
    assert ask_user.invoke({"question": "which range?"}) == "next quarter"


def test_returns_answer_from_wrapped_draft(monkeypatch):
    monkeypatch.setattr(lt, "interrupt", lambda _p: {"draft": {"answer": "TVs"}})
    assert ask_user.invoke({"question": "which product?"}) == "TVs"


def test_returns_raw_string_answer(monkeypatch):
    monkeypatch.setattr(lt, "interrupt", lambda _p: "just text")
    assert ask_user.invoke({"question": "q"}) == "just text"


def test_empty_answer_is_empty_string(monkeypatch):
    monkeypatch.setattr(lt, "interrupt", lambda _p: None)
    assert ask_user.invoke({"question": "q"}) == ""
