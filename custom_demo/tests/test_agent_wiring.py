"""Deterministic tests for agent wiring.

Data-prompt gap, tool selection, and the capability note: pure functions and
hand-built requests, no LLM, no network.
"""

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT
from langchain_core.tools import BaseTool
from langgraph.graph import START, StateGraph
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from pydantic import BaseModel, ValidationError

from custom_demo.core.ctx import Context
from custom_demo.runtime import agent as A
from custom_demo.runtime.tools.registry import (
    HITL_IDS,
    all_tools,
    allowed_tool_names,
    is_allowed,
    subagent_tools,
)

# --- tool selection (#12) ---


def test_allowed_tool_names_and_is_allowed():
    allowed = allowed_tool_names(["web_search"])  # only web_search enabled
    assert is_allowed("web_search", allowed)
    assert not is_allowed("draft_email", allowed)  # catalogue tool, disabled -> hidden
    assert is_allowed("task", allowed)  # deepagents built-in → untouched
    assert is_allowed(None, allowed)


class _FakeReq:
    """Minimal ModelRequest stand-in for ToolSelection._apply."""

    def __init__(self, tool_names, enabled):
        self.tools = [SimpleNamespace(name=n) for n in tool_names]
        self.runtime = SimpleNamespace(context=Context(enabled_tools=enabled))
        self.overridden: dict[str, Any] | None = None

    def override(self, **kw):
        self.overridden = kw
        return self


def test_tool_selection_drops_disabled_catalogue_tools_only():
    req = _FakeReq(["web_search", "draft_email", "task"], ["web_search"])
    A.ToolSelection()._apply(cast("Any", req))
    assert req.overridden is not None  # a filter happened
    kept = {t.name for t in req.overridden["tools"]}
    assert kept == {"web_search", "task"}  # draft_email filtered, built-in kept


def test_tool_selection_no_override_when_nothing_filtered():
    req = _FakeReq(["web_search", "push_widget"], ["web_search", "push_widget"])
    A.ToolSelection()._apply(cast("Any", req))
    assert req.overridden is None  # common path: request returned unchanged


# --- capability note flags dashboards off (#17) ---


def _rt(enabled):
    return SimpleNamespace(context=Context(enabled_tools=enabled))


def test_capability_note_flags_dashboards_off_when_push_widget_disabled():
    assert "DASHBOARDS ARE OFF" in A._capability_note(_rt(["web_search"]))


def test_capability_note_no_dashboards_off_when_push_widget_enabled():
    assert "DASHBOARDS ARE OFF" not in A._capability_note(_rt(["web_search", "push_widget"]))


def test_capability_note_empty_for_unset_selection():
    assert A._capability_note(_rt(None)) == ""  # default selection → no note (unchanged path)


# --- the composed prompt says ONE thing about widgets ---


class _PromptReq:
    """Minimal ModelRequest stand-in for the `@dynamic_prompt` middleware."""

    def __init__(self, enabled):
        self.runtime = _rt(enabled)
        self.system_prompt = ""
        self.system_message: Any = None

    def override(self, **kw):
        self.system_message = kw["system_message"]
        return self


def _composed_prompt(enabled) -> str:
    req = _PromptReq(enabled)
    A._hub_system_prompt.wrap_model_call(cast("Any", req), lambda r: r)
    return req.system_message.text


def test_composed_prompt_drops_widget_guidance_when_push_widget_is_off(monkeypatch):
    """Dashboards off must not arrive alongside "widgets are how you answer"."""
    monkeypatch.setenv("SANDBOX_ENABLED", "1")
    prompt = _composed_prompt(["web_search"])
    assert "DASHBOARDS ARE OFF" in prompt
    assert "WIDGETS FIRST" not in prompt
    assert "visualize it with `push_widget`" not in prompt
    assert "HTML artifacts" in prompt  # artifact authoring survives without widgets


def test_composed_prompt_keeps_widget_guidance_when_push_widget_is_on(monkeypatch):
    monkeypatch.setenv("SANDBOX_ENABLED", "1")
    prompt = _composed_prompt(["web_search", "push_widget"])
    assert "DASHBOARDS ARE OFF" not in prompt
    assert "WIDGETS FIRST" in prompt
    assert "visualize it with `push_widget`" in prompt


def test_composed_prompt_keeps_widget_guidance_for_the_default_selection(monkeypatch):
    """`push_widget` is on by default, so the common path is unchanged."""
    monkeypatch.setenv("SANDBOX_ENABLED", "1")
    prompt = _composed_prompt(None)
    assert "WIDGETS FIRST" in prompt
    assert "visualize it with `push_widget`" in prompt


# --- dynamic subagents are part of every agent ---


def test_subagents_note_always_explains_orchestration():
    note = A._subagents_note()
    assert "task()" in note and "orchestrat" in note.lower()
    assert "researcher" in note and "analyst" in note
    assert "Python `execute`" in note
    assert "Don't over-orchestrate simple requests" in note


@pytest.mark.parametrize("deployed", [False, True])
def test_every_build_installs_the_interpreter_before_final_tool_selection(monkeypatch, deployed):
    captured = {}
    monkeypatch.setattr(A, "require_model_key", lambda *_: None)
    monkeypatch.setattr(A, "build_chat_model", lambda *_: None)
    monkeypatch.setattr(A, "create_deep_agent", lambda **kwargs: captured.update(kwargs))

    A.build_agent(deployed=deployed)

    middleware_names = [type(middleware).__name__ for middleware in captured["middleware"]]
    assert middleware_names[0] == "RubricMiddleware"
    assert middleware_names[-2:] == ["CodeInterpreterMiddleware", "ToolSelection"]
    assert middleware_names.count("CodeInterpreterMiddleware") == 1
    specs = {spec["name"]: spec for spec in captured["subagents"]}
    assert set(specs) == {"researcher", "analyst", "general-purpose"}
    for spec in specs.values():
        assert not _offered_to(spec) & HITL_IDS

    assert specs["general-purpose"]["skills"] == captured["skills"]


def test_a_required_interpreter_failure_stops_graph_construction(monkeypatch):
    monkeypatch.setattr(A, "require_model_key", lambda *_: None)
    monkeypatch.setattr(A, "build_chat_model", lambda *_: None)
    import langchain_quickjs  # noqa: PLC0415

    cause = RuntimeError("no quickjs-rs wheel for this platform")

    def _boom(*_a, **_k):
        raise cause

    monkeypatch.setattr(langchain_quickjs, "CodeInterpreterMiddleware", _boom)
    with pytest.raises(A.DynamicSubagentsError, match="required QuickJS") as exc:
        A.build_agent(deployed=True)

    assert "no quickjs-rs wheel for this platform" in str(exc.value)
    assert "Unset" not in str(exc.value)
    assert exc.value.__cause__ is cause


# --- goal grading (RubricMiddleware, drives the SPA's goal pill) ---


def test_rubric_middleware_is_built_with_the_goal_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("GOAL_MAX_ITERATIONS", "2")
    mw = A._rubric_middleware()
    assert mw is not None
    assert mw.max_iterations == 2


def test_graph_accepts_a_rubric_on_its_input(monkeypatch):
    """The SPA sends the user's `/goal` as the run's `rubric`.

    If it isn't in the input schema the server drops it and nothing is graded.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    props = A.build_agent(deployed=True).get_input_jsonschema()["properties"]
    assert "rubric" in props


def test_a_grader_that_cannot_be_built_fails_the_graph_instead_of_going_quiet(monkeypatch):
    """Replaces a test that asserted the graph built anyway, with `rubric` dropped.

    That was the old guarded behaviour: `_rubric_middleware` returned None and the
    deployment came up with a goal pill that accepted a goal and then never graded
    it, with nothing saying why. `RubricMiddleware` comes from a hard-pinned
    deepagents, so the only way this fails now is a broken deployment, which should
    look like one.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def _boom(**_):
        raise RuntimeError("no grader model")

    monkeypatch.setattr(A, "RubricMiddleware", _boom)
    with pytest.raises(RuntimeError, match="no grader model"):
        A.build_agent(deployed=True)


def test_the_goal_grader_is_always_in_the_graph(monkeypatch):
    """`rubric` is in the input schema unconditionally — it is not opt-in."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert "rubric" in A.build_agent(deployed=True).get_input_jsonschema()["properties"]


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
    names = _bound_tool_names(A.build_agent(deployed=True))
    assert "write_todos" not in names  # TodoListMiddleware not opted in on 0.7
    # Sanity: other built-ins/tools are present (only the todo one is absent).
    assert {"task", "read_file", "push_widget"} <= names


# --- MCP tools are bound per run, not at graph build (#mcp) ---


class _McpReq:
    """Minimal ModelRequest stand-in for McpTools.awrap_model_call."""

    def __init__(self, tool_names, servers):
        self.tools = [SimpleNamespace(name=n) for n in tool_names]
        self.runtime = SimpleNamespace(context=Context(mcp_servers=servers))

    def override(self, **kw):
        self.tools = kw.get("tools", self.tools)
        return self


_SERVER = [{"label": "Fieldlink", "url": "https://x.ngrok.app/mcp"}]


def _stub_tools(monkeypatch, tools):
    """Stand in for discovery, so these tests never touch a network."""

    async def load(_servers, **_kw):
        return tools

    # Patched on agent.py: that is where the name is looked up, now that it is a
    # top-level import there rather than a call-time one.
    monkeypatch.setattr(A, "load_tools", load)


def test_mcp_tools_are_offered_alongside_the_built_in_ones(monkeypatch):
    remote = SimpleNamespace(name="fieldlink_get_shipment")
    _stub_tools(monkeypatch, [remote])
    req = _McpReq(["web_search"], _SERVER)

    async def handler(r):
        return r

    out = asyncio.run(A.McpTools().awrap_model_call(cast("Any", req), handler))
    assert [t.name for t in out.tools] == ["web_search", "fieldlink_get_shipment"]


def test_no_mcp_server_configured_leaves_the_request_untouched(monkeypatch):
    _stub_tools(monkeypatch, [])
    req = _McpReq(["web_search"], None)

    async def handler(r):
        return r

    out = asyncio.run(A.McpTools().awrap_model_call(cast("Any", req), handler))
    assert [t.name for t in out.tools] == ["web_search"]


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
        runtime=cast("Any", SimpleNamespace(context=Context(mcp_servers=_SERVER))),
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
    ours = SimpleNamespace(name="web_search")
    request = ToolCallRequest(
        tool_call={"name": "web_search", "args": {}, "id": "1", "type": "tool_call"},
        tool=cast("Any", ours),
        state={},
        runtime=cast("Any", SimpleNamespace(context=Context(mcp_servers=_SERVER))),
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
        note = A._mcp_note(SimpleNamespace(context=Context(mcp_servers=_SERVER)))
    finally:
        A._mcp_tools.reset(token)

    assert "Fieldlink" in note
    assert "`fieldlink_get_shipment` (Fieldlink): Look one consignment up." in note


def test_mcp_note_is_empty_without_mcp_tools():
    assert A._mcp_note(SimpleNamespace(context=Context(mcp_servers=None))) == ""


def test_mcp_note_says_a_configured_server_could_not_be_reached():
    """`load_tools` never raises, so an unreachable server yielded silence.

    The user typed that URL into the SPA. An agent answering as though it has no
    connected systems is indistinguishable from a broken one, so the model is told
    and can say so. The turn still runs.
    """
    token = A._mcp_tools.set(())  # discovery ran, produced nothing
    try:
        note = A._mcp_note(SimpleNamespace(context=Context(mcp_servers=_SERVER)))
    finally:
        A._mcp_tools.reset(token)

    assert "CONNECTED SYSTEMS UNAVAILABLE" in note
    assert "Fieldlink" in note
    assert "do NOT invent" in note  # ...and must not answer from local files instead


def test_mcp_note_stays_quiet_on_the_sync_path():
    # None, not an empty tuple: no discovery ran, so a sync run knows nothing either
    # way and must not claim the server is down.
    assert A._mcp_note(SimpleNamespace(context=Context(mcp_servers=_SERVER))) == ""


def test_mcp_note_says_nothing_when_no_server_is_configured():
    token = A._mcp_tools.set(())
    try:
        assert A._mcp_note(SimpleNamespace(context=Context(mcp_servers=None))) == ""
    finally:
        A._mcp_tools.reset(token)


# --- context arrives as a Context, whatever shape the caller sent ---


class _ProbeState(BaseModel):
    """Graph state for the probe below. These two exercise the context, not the state."""

    done: bool = False


def _context_probe():
    """A one-node graph whose node records the `Context` the run resolved."""
    seen: dict[str, Any] = {}

    def node(state: _ProbeState, runtime: Runtime[Context]) -> _ProbeState:
        seen["context"] = runtime.context
        return state

    graph = StateGraph(_ProbeState, context_schema=Context)
    graph.add_node("n", node)
    graph.add_edge(START, "n")
    return graph.compile(), seen


def test_a_dict_context_reaches_a_node_as_a_context_object():
    """The deployment sends `{"context": {...}}` on the wire; this is what it becomes.

    Nothing else covers the shape a real run arrives in: every other test here hands a
    node a `Context` it built itself. LangGraph coerces the dict against the
    `context_schema`, which is what lets every reader use dot notation, and an unknown
    key (the SPA sends `ls_project` for trace routing) rides along without failing.
    """
    app, seen = _context_probe()
    app.invoke(_ProbeState(), context=cast("Any", {"customer": "Acme", "ls_project": "p"}))

    assert isinstance(seen["context"], Context)
    assert seen["context"].customer == "Acme"
    assert seen["context"].agent_repo is None  # unset fields take the declared default


def test_a_malformed_stored_context_fails_at_run_start_naming_the_field():
    """A validated `context_schema` is what makes every reader's dot notation safe.

    A `sandbox_seed` that is not a list used to reach `_sandbox_note`, which re-checked
    the type it had already been promised. Validation moves the failure to the run
    boundary, where the message names the field an operator has to fix.
    """
    app, _seen = _context_probe()
    with pytest.raises(ValidationError) as excinfo:
        app.invoke(_ProbeState(), context=cast("Any", {"sandbox_seed": "not-a-list"}))

    assert "sandbox_seed" in str(excinfo.value)


# --- subagents cannot pause the run ---


def _offered_to(spec) -> set[str]:
    """The tool names offered by this app's explicitly declared BaseTool instances."""
    return {cast("BaseTool", t).name for t in spec["tools"]}


def test_every_subagent_spec_declares_its_own_tools():
    specs = A._subagent_specs()
    assert specs
    for spec in specs:
        assert "tools" in spec, f"{spec['name']} would inherit every main-agent tool"


def test_no_subagent_can_reach_a_human_in_the_loop_tool():
    for spec in A._subagent_specs():
        leaked = _offered_to(spec) & HITL_IDS
        assert not leaked, f"{spec['name']} can pause the run via {sorted(leaked)}"


def test_the_main_agent_keeps_the_tools_its_subagents_lose():
    main = {t.name for t in all_tools()}
    assert HITL_IDS <= main
    assert {t.name for t in subagent_tools()} == main - HITL_IDS


def test_general_purpose_declares_its_skill_sources():
    specs = {spec["name"]: spec for spec in A._subagent_specs()}
    assert GENERAL_PURPOSE_SUBAGENT["name"] in specs
    assert specs[GENERAL_PURPOSE_SUBAGENT["name"]].get("skills") == list(A._SKILL_SOURCES)


def test_all_specialists_are_always_available():
    assert [spec["name"] for spec in A._subagent_specs()] == [
        "researcher",
        "analyst",
        GENERAL_PURPOSE_SUBAGENT["name"],
    ]


@pytest.mark.parametrize("deployed", [False, True])
def test_the_built_graph_has_an_interpreter_and_safe_subagents(monkeypatch, deployed):
    """Inspect the real compiled children, not just the input specifications."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    import deepagents.middleware.subagents as subagents_module  # noqa: PLC0415

    compiled: dict[str, set[str]] = {}
    real = subagents_module.create_agent

    def spy(model, **kwargs):
        compiled[kwargs["name"]] = {cast("BaseTool", t).name for t in (kwargs.get("tools") or [])}
        return real(model, **kwargs)

    monkeypatch.setattr(subagents_module, "create_agent", spy)
    graph = A.build_agent(deployed=deployed)

    assert set(compiled) == {"researcher", "analyst", GENERAL_PURPOSE_SUBAGENT["name"]}
    for name, offered in compiled.items():
        assert not offered & HITL_IDS, f"{name} was compiled with {sorted(offered & HITL_IDS)}"

    names = _bound_tool_names(graph)
    assert {"eval", "task", "read_file", "push_widget"} <= names
    assert "write_todos" not in names


def test_a_subagent_is_told_it_has_nobody_to_ask():
    for spec in A._subagent_specs():
        prompt = spec["system_prompt"]
        assert "working alone" in prompt, spec["name"]
        assert "reply IS the deliverable" in prompt, spec["name"]
