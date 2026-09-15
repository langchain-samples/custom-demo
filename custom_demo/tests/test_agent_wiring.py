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


# --- dynamic-subagents gate (DYNAMIC_SUBAGENTS, build-time env) ---


def test_subagents_note_off_by_default(monkeypatch):
    monkeypatch.delenv("DYNAMIC_SUBAGENTS", raising=False)
    assert A._subagents_note() == ""  # gated off → no orchestration note


def test_subagents_note_on_when_enabled(monkeypatch):
    monkeypatch.setenv("DYNAMIC_SUBAGENTS", "1")
    note = A._subagents_note()
    assert "task()" in note and "orchestrat" in note.lower()  # distinguishes JS orchestration
    assert "execute" in note  # ...from the Python data sandbox


def test_subagents_that_cannot_be_built_fail_the_flag_that_asked_for_them(monkeypatch):
    """An OPT-IN capability must not opt itself back out.

    This used to catch every exception and set `subagents = None`, so an operator who
    deliberately set DYNAMIC_SUBAGENTS got an agent with no subagents, no error, and
    skills whose workflows tell it to fan work out to them. `langchain-quickjs` is a
    hard pin, so a failure here is a broken install and the operator who set the flag
    is the one who can act on the message.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("DYNAMIC_SUBAGENTS", "1")
    import langchain_quickjs  # noqa: PLC0415

    def _boom(*_a, **_k):
        raise RuntimeError("no quickjs-rs wheel for this platform")

    monkeypatch.setattr(langchain_quickjs, "CodeInterpreterMiddleware", _boom)
    with pytest.raises(A.DynamicSubagentsError, match="DYNAMIC_SUBAGENTS"):
        A.build_agent(deployed=True)


def test_the_subagent_failure_names_the_underlying_cause(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("DYNAMIC_SUBAGENTS", "1")
    import langchain_quickjs  # noqa: PLC0415

    def _boom(*_a, **_k):
        raise RuntimeError("no quickjs-rs wheel")

    monkeypatch.setattr(langchain_quickjs, "CodeInterpreterMiddleware", _boom)
    with pytest.raises(A.DynamicSubagentsError) as exc:
        A.build_agent(deployed=True)

    assert "no quickjs-rs wheel" in str(exc.value)
    assert isinstance(exc.value.__cause__, RuntimeError)


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
    monkeypatch.setenv("DYNAMIC_SUBAGENTS", "0")
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
    monkeypatch.setenv("DYNAMIC_SUBAGENTS", "0")

    def _boom(**_):
        raise RuntimeError("no grader model")

    monkeypatch.setattr(A, "RubricMiddleware", _boom)
    with pytest.raises(RuntimeError, match="no grader model"):
        A.build_agent(deployed=True)


def test_the_goal_grader_is_always_in_the_graph(monkeypatch):
    """`rubric` is in the input schema unconditionally — it is not opt-in."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("DYNAMIC_SUBAGENTS", "0")
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
    monkeypatch.setenv("DYNAMIC_SUBAGENTS", "0")
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

# Both build paths. `DYNAMIC_SUBAGENTS` off is the DEFAULT and the one that hid this:
# with no specs of ours, deepagents fills in a general-purpose subagent holding every
# main-agent tool, and `FilesystemMiddleware` offers `task` either way, so the flag
# being off never meant "no subagent to worry about".
BOTH_PATHS = [True, False]


def _offered_to(spec) -> set[str]:
    """The tool names a subagent spec offers its model.

    `SubAgent.tools` is typed to accept bare callables and dicts as well as tools.
    Everything this app puts there is a `BaseTool`, so read `.name` through a cast
    rather than widening the assertions to cope with shapes that never arrive.
    """
    return {cast("BaseTool", t).name for t in spec["tools"]}


@pytest.mark.parametrize("dynamic", BOTH_PATHS)
def test_every_subagent_spec_declares_its_own_tools(dynamic):
    """A spec that omits `tools` inherits the main agent's entire list.

    That default is what put `ask_user` inside an `analyst`, where a placeholder
    question interrupted the graph and the fan-out above it never resumed: the run was
    stranded on mid-workflow narration with the answer never delivered. The key is
    stamped centrally, so this asserts the stamping reached every spec rather than
    trusting each literal to remember it.
    """
    specs = A._subagent_specs(dynamic=dynamic)
    assert specs, "there is always at least general-purpose, so always something to constrain"
    for spec in specs:
        assert "tools" in spec, f"{spec['name']} would inherit every main-agent tool"


@pytest.mark.parametrize("dynamic", BOTH_PATHS)
def test_no_subagent_can_reach_a_human_in_the_loop_tool(dynamic):
    for spec in A._subagent_specs(dynamic=dynamic):
        leaked = _offered_to(spec) & HITL_IDS
        assert not leaked, f"{spec['name']} can pause the run via {sorted(leaked)}"


def test_the_main_agent_keeps_the_tools_its_subagents_lose():
    """The fix withholds pausing from subagents ONLY.

    `ask_user` is always-on for the main agent, which is the one turn a client can
    answer, so a change that took it away everywhere would pass the test above and
    still be wrong.
    """
    main = {t.name for t in all_tools()}
    assert HITL_IDS <= main
    assert {t.name for t in subagent_tools()} == main - HITL_IDS


@pytest.mark.parametrize("dynamic", BOTH_PATHS)
def test_general_purpose_is_declared_on_both_paths(dynamic):
    """The framework appends its own `general-purpose` when the caller names none.

    That auto-added copy is built from the main agent's tools, so it carries the same
    fault the other specs were fixed for, and no assertion about `_SUBAGENTS` alone
    would catch it. Declaring it is what replaces that copy; `skills` is re-declared
    with it because an inline spec only mounts the sources it asks for.
    """
    specs = {spec["name"]: spec for spec in A._subagent_specs(dynamic=dynamic)}
    assert GENERAL_PURPOSE_SUBAGENT["name"] in specs
    assert specs[GENERAL_PURPOSE_SUBAGENT["name"]].get("skills") == list(A._SKILL_SOURCES)


def test_the_specialists_stay_behind_the_flag():
    """Only `general-purpose` is unconditional.

    `researcher` and `analyst` exist to be fanned out to by the QuickJS orchestration
    script, so offering them without the interpreter would advertise dispatch targets
    the prompt never explains (`_subagents_note` is gated on the same flag).
    """
    off = {spec["name"] for spec in A._subagent_specs(dynamic=False)}
    on = {spec["name"] for spec in A._subagent_specs(dynamic=True)}
    assert off == {GENERAL_PURPOSE_SUBAGENT["name"]}
    assert on - off == {"researcher", "analyst"}


@pytest.mark.parametrize("dynamic", BOTH_PATHS)
def test_the_built_graph_never_hands_a_subagent_a_pausing_tool(dynamic, monkeypatch):
    """The end-to-end guard, on the tools deepagents actually compiles the subagent with.

    Everything above reads our specs. This reads what the framework did with them, so
    a deepagents change that stopped honouring a spec's `tools`, or renamed the default
    subagent our name-match suppresses, fails here instead of shipping a subagent that
    quietly regained `ask_user`.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("DYNAMIC_SUBAGENTS", "1" if dynamic else "0")
    import deepagents.middleware.subagents as subagents_module  # noqa: PLC0415

    compiled: dict[str, set[str]] = {}
    real = subagents_module.create_agent

    def spy(model, **kwargs):
        # Subscript, not `.get`: every subagent compile passes a name, and a deepagents
        # that stopped should fail here rather than collapse the specs onto one key.
        compiled[kwargs["name"]] = {cast("BaseTool", t).name for t in (kwargs.get("tools") or [])}
        return real(model, **kwargs)

    monkeypatch.setattr(subagents_module, "create_agent", spy)
    A.build_agent(deployed=True)

    assert compiled, "no subagent was compiled, so this asserted nothing"
    assert GENERAL_PURPOSE_SUBAGENT["name"] in compiled  # the default was replaced, not added to
    for name, offered in compiled.items():
        assert not offered & HITL_IDS, f"{name} was compiled with {sorted(offered & HITL_IDS)}"


@pytest.mark.parametrize("dynamic", BOTH_PATHS)
def test_a_subagent_is_told_it_has_nobody_to_ask(dynamic):
    """Withholding the tool stops the interrupt; the prompt stops the wasted reach.

    Also the half that addresses the narration itself: a subagent whose reply is a
    progress note leaves its caller nothing to synthesize, whether or not it paused.
    """
    for spec in A._subagent_specs(dynamic=dynamic):
        prompt = spec["system_prompt"]
        assert "working alone" in prompt, spec["name"]
        assert "reply IS the deliverable" in prompt, spec["name"]
