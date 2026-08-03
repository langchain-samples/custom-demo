"""Deterministic tests for agent wiring.

Data-prompt gap, tool selection, and the capability note: pure functions and
hand-built requests, no LLM, no network.
"""

from types import SimpleNamespace
from typing import Any, cast

from dashboard_agent import agent as A
from dashboard_agent.prompt import build_data_prompt
from dashboard_agent.tools.registry import allowed_tool_names, is_allowed

# --- data-llm withholds the planted gap (#9) ---


def test_data_prompt_withholds_the_gap():
    p = build_data_prompt("customer satisfaction scores", "Acme", "Retail")
    assert "customer satisfaction scores" in p
    assert "WITHHELD DATA" in p
    assert "return empty results" in p  # instructs the synthetic source to return nothing


# --- tool selection (#12) ---


def test_allowed_tool_names_and_is_allowed():
    allowed = allowed_tool_names(["datasearch"])  # only datasearch enabled
    assert is_allowed("datasearch", allowed)
    assert not is_allowed("web_search", allowed)  # catalogue tool, disabled → hidden
    assert is_allowed("write_todos", allowed)  # deepagents built-in → untouched
    assert is_allowed(None, allowed)


class _FakeReq:
    """Minimal ModelRequest stand-in for ToolSelection._apply."""

    def __init__(self, tool_names, enabled):
        self.tools = [SimpleNamespace(name=n) for n in tool_names]
        self.runtime = SimpleNamespace(context={"enabled_tools": enabled})
        self.overridden: dict[str, Any] | None = None

    def override(self, **kw):
        self.overridden = kw
        return self


def test_tool_selection_drops_disabled_catalogue_tools_only():
    req = _FakeReq(["datasearch", "web_search", "write_todos"], ["datasearch"])
    A.ToolSelection()._apply(cast("Any", req))
    assert req.overridden is not None  # a filter happened
    kept = {t.name for t in req.overridden["tools"]}
    assert kept == {"datasearch", "write_todos"}  # web_search filtered, built-in kept


def test_tool_selection_no_override_when_nothing_filtered():
    req = _FakeReq(["datasearch", "push_widget"], ["datasearch", "push_widget"])
    A.ToolSelection()._apply(cast("Any", req))
    assert req.overridden is None  # common path: request returned unchanged


# --- capability note flags dashboards off (#17) ---


def _rt(enabled):
    return SimpleNamespace(context={"enabled_tools": enabled})


def test_capability_note_flags_dashboards_off_when_push_widget_disabled():
    assert "DASHBOARDS ARE OFF" in A._capability_note(_rt(["datasearch"]))


def test_capability_note_no_dashboards_off_when_push_widget_enabled():
    assert "DASHBOARDS ARE OFF" not in A._capability_note(_rt(["datasearch", "push_widget"]))


def test_capability_note_empty_for_unset_selection():
    assert A._capability_note(_rt(None)) == ""  # default selection → no note (unchanged path)


# --- dynamic-subagents gate (DA_DYNAMIC_SUBAGENTS, build-time env) ---


def test_subagents_note_off_by_default(monkeypatch):
    monkeypatch.delenv("DA_DYNAMIC_SUBAGENTS", raising=False)
    assert A._subagents_note() == ""  # gated off → no orchestration note


def test_subagents_note_on_when_enabled(monkeypatch):
    monkeypatch.setenv("DA_DYNAMIC_SUBAGENTS", "1")
    note = A._subagents_note()
    assert "task()" in note and "orchestrat" in note.lower()  # distinguishes JS orchestration
    assert "execute" in note  # ...from the Python data sandbox


# --- no todo middleware (deepagents 0.7 makes it opt-in; we don't opt in) ---


def _bound_tool_names(compiled) -> set[str]:
    """Every tool name bound anywhere in a compiled deep-agent graph."""
    names: set[str] = set()
    for node in compiled.nodes.values():
        bound = getattr(node, "bound", None)
        by_name = getattr(bound, "tools_by_name", None)
        if isinstance(by_name, dict):
            names.update(by_name.keys())
        for t in getattr(bound, "tools", None) or []:
            n = getattr(t, "name", None)
            if n:
                names.add(n)
    return names


def test_build_graph_has_no_write_todos_tool(monkeypatch):
    # Compiling the graph never calls the model, so a fake key is enough and this
    # runs in key-stripped CI. Asserts the real assembled harness, not just config.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("DA_DYNAMIC_SUBAGENTS", "0")
    names = _bound_tool_names(A.build_graph())
    assert "write_todos" not in names  # TodoListMiddleware not opted in on 0.7
    # Sanity: other built-ins/tools are present (only the todo one is absent).
    assert {"task", "read_file", "datasearch"} <= names
