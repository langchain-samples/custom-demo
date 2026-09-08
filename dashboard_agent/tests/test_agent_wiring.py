"""Deterministic tests for agent wiring.

Data-prompt gap, tool selection, and the capability note: pure functions and
hand-built requests, no LLM, no network.
"""

import asyncio
from types import SimpleNamespace
from typing import Any, cast

from langgraph.prebuilt.tool_node import ToolCallRequest

from dashboard_agent.runtime import agent as A
from dashboard_agent.runtime.tools.registry import allowed_tool_names, is_allowed

# --- tool selection (#12) ---


def test_allowed_tool_names_and_is_allowed():
    allowed = allowed_tool_names(["web_search"])  # only web_search enabled
    assert is_allowed("web_search", allowed)
    assert not is_allowed("draft_email", allowed)  # catalogue tool, disabled -> hidden
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
    req = _FakeReq(["web_search", "draft_email", "write_todos"], ["web_search"])
    A.ToolSelection()._apply(cast("Any", req))
    assert req.overridden is not None  # a filter happened
    kept = {t.name for t in req.overridden["tools"]}
    assert kept == {"web_search", "write_todos"}  # draft_email filtered, built-in kept


def test_tool_selection_no_override_when_nothing_filtered():
    req = _FakeReq(["web_search", "push_widget"], ["web_search", "push_widget"])
    A.ToolSelection()._apply(cast("Any", req))
    assert req.overridden is None  # common path: request returned unchanged


# --- capability note flags dashboards off (#17) ---


def _rt(enabled):
    return SimpleNamespace(context={"enabled_tools": enabled})


def test_capability_note_flags_dashboards_off_when_push_widget_disabled():
    assert "DASHBOARDS ARE OFF" in A._capability_note(_rt(["web_search"]))


def test_capability_note_no_dashboards_off_when_push_widget_enabled():
    assert "DASHBOARDS ARE OFF" not in A._capability_note(_rt(["web_search", "push_widget"]))


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


# --- goal grading (RubricMiddleware, drives the SPA's goal pill) ---


def test_rubric_middleware_is_built_with_the_goal_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("DASHBOARD_GOAL_MAX_ITERATIONS", "2")
    mw = A._rubric_middleware()
    assert mw is not None
    assert mw.max_iterations == 2


def test_graph_accepts_a_rubric_on_its_input(monkeypatch):
    """The SPA sends the user's `/goal` as the run's `rubric`.

    If it isn't in the input schema the server drops it and nothing is graded.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("DA_DYNAMIC_SUBAGENTS", "0")
    props = A.build_agent(deployed=True).get_input_jsonschema()["properties"]
    assert "rubric" in props


def test_graph_still_builds_when_the_rubric_middleware_cannot_be_made(monkeypatch):
    """An optional capability must never take graph load down with it."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("DA_DYNAMIC_SUBAGENTS", "0")
    monkeypatch.setattr(A, "_rubric_middleware", lambda: None)
    assert "rubric" not in A.build_agent(deployed=True).get_input_jsonschema()["properties"]


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


def test_deployed_agent_has_no_write_todos_tool(monkeypatch):
    # Compiling the graph never calls the model, so a fake key is enough and this
    # runs in key-stripped CI. Asserts the real assembled harness, not just config.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("DA_DYNAMIC_SUBAGENTS", "0")
    names = _bound_tool_names(A.build_agent(deployed=True))
    assert "write_todos" not in names  # TodoListMiddleware not opted in on 0.7
    # Sanity: other built-ins/tools are present (only the todo one is absent).
    assert {"task", "read_file", "push_widget"} <= names


# --- MCP tools are bound per run, not at graph build (#mcp) ---


class _McpReq:
    """Minimal ModelRequest stand-in for McpTools.awrap_model_call."""

    def __init__(self, tool_names, servers):
        self.tools = [SimpleNamespace(name=n) for n in tool_names]
        self.runtime = SimpleNamespace(context={"mcp_servers": servers})

    def override(self, **kw):
        self.tools = kw.get("tools", self.tools)
        return self


_SERVER = [{"label": "Fieldlink", "url": "https://x.ngrok.app/mcp"}]


def _stub_tools(monkeypatch, tools):
    """Stand in for discovery, so these tests never touch a network."""

    async def load(_servers, **_kw):
        return tools

    monkeypatch.setattr("dashboard_agent.runtime.mcp_servers.load_tools", load)


def test_mcp_tools_are_offered_alongside_the_built_in_ones(monkeypatch):
    remote = SimpleNamespace(name="fieldlink_get_shipment")
    _stub_tools(monkeypatch, [remote])
    req = _McpReq(["datasearch"], _SERVER)

    async def handler(r):
        return r

    out = asyncio.run(A.McpTools().awrap_model_call(cast("Any", req), handler))
    assert [t.name for t in out.tools] == ["datasearch", "fieldlink_get_shipment"]


def test_no_mcp_server_configured_leaves_the_request_untouched(monkeypatch):
    _stub_tools(monkeypatch, [])
    req = _McpReq(["datasearch"], None)

    async def handler(r):
        return r

    out = asyncio.run(A.McpTools().awrap_model_call(cast("Any", req), handler))
    assert [t.name for t in out.tools] == ["datasearch"]


def test_an_mcp_tool_call_is_given_the_tool_the_tool_node_lacks(monkeypatch):
    """ToolNode passes `tool=None` for a name it was not built with.

    Substituting the real tool here is the whole reason an MCP call executes
    instead of coming back as "tool not found".
    """
    remote = SimpleNamespace(name="fieldlink_get_shipment")
    _stub_tools(monkeypatch, [remote])
    request = ToolCallRequest(
        tool_call={"name": "fieldlink_get_shipment", "args": {}, "id": "1", "type": "tool_call"},
        tool=None,
        state={},
        runtime=cast("Any", SimpleNamespace(context={"mcp_servers": _SERVER})),
    )

    seen = {}

    async def handler(r):
        seen["tool"] = r.tool
        return "ran"

    assert asyncio.run(A.McpTools().awrap_tool_call(request, handler)) == "ran"
    assert seen["tool"] is remote


def test_a_registered_tool_is_left_alone(monkeypatch):
    """Our own tools already carry a tool object; the hook must not swap them."""
    _stub_tools(monkeypatch, [SimpleNamespace(name="fieldlink_get_shipment")])
    ours = SimpleNamespace(name="datasearch")
    request = ToolCallRequest(
        tool_call={"name": "datasearch", "args": {}, "id": "1", "type": "tool_call"},
        tool=cast("Any", ours),
        state={},
        runtime=cast("Any", SimpleNamespace(context={"mcp_servers": _SERVER})),
    )

    seen = {}

    async def handler(r):
        seen["tool"] = r.tool
        return "ran"

    asyncio.run(A.McpTools().awrap_tool_call(request, handler))
    assert seen["tool"] is ours


def test_mcp_note_names_the_server_behind_each_tool(monkeypatch):
    remote = SimpleNamespace(
        name="fieldlink_get_shipment", description="Look one consignment up.\nMore detail."
    )
    token = A._mcp_tools.set((remote,))
    try:
        note = A._mcp_note(SimpleNamespace(context={"mcp_servers": _SERVER}))
    finally:
        A._mcp_tools.reset(token)
    assert "Fieldlink" in note
    assert "`fieldlink_get_shipment` (Fieldlink): Look one consignment up." in note


def test_mcp_note_is_empty_without_mcp_tools():
    assert A._mcp_note(SimpleNamespace(context={"mcp_servers": None})) == ""
