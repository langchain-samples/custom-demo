"""Remote A2A agents as per-run subagents: config parsing, the dynamic `task`, and wake-up.

The behaviours pinned here are the ones a demo depends on and a refactor could
silently break: a remote agent appears in `task` next to the built-in subagents
without the built-ins changing, interpreter code's `task()` sees it too, every tool
receives its runtime, and a background task that finishes reports back to the thread
that started it.
"""

from __future__ import annotations

import asyncio
import dataclasses
from types import SimpleNamespace
from typing import Annotated

import pytest
from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.config import get_config
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command
from langsmith import tracing_context
from langsmith.run_trees import RunTree
from pydantic import BaseModel, Field

from custom_demo.runtime import remote_agents as ra
from custom_demo.runtime import remote_subagents as rs

AGENT = ra.RemoteAgent(
    name="order_tracker",
    label="Order and Supply Tracker",
    description="Tracks customer hardware orders.",
    endpoint="http://agents.test/a2a/abc",
    headers={},
)


def _static_task() -> StructuredTool:
    """A stand-in for the built-in `task` tool that records what it was asked."""
    calls: list[str] = []

    def task(description: str, subagent_type: str, runtime: ToolRuntime) -> str:
        calls.append(subagent_type)
        return f"static:{subagent_type}"

    async def atask(description: str, subagent_type: str, runtime: ToolRuntime) -> str:
        calls.append(subagent_type)
        return f"static:{subagent_type}"

    tool = StructuredTool.from_function(
        name="task",
        func=task,
        coroutine=atask,
        description="Built-in subagents:\n- general-purpose: x",
    )
    tool.metadata = {"calls": calls}
    return tool


def _fake_stream(answer: str):
    """A `stream_message` stand-in that plays a short, well-formed A2A event sequence."""

    async def stream(agent, text, context_id, trace=None, task_id=None, resume=None):
        yield {"task": {"id": "ctx:run", "status": {"state": "TASK_STATE_SUBMITTED"}}}
        tool = {"tool_results": [{"content": "read /data/orders.csv\nmore"}]}
        yield {
            "kind": "status-update",
            "status": {"state": "TASK_STATE_WORKING", "message": {"parts": [{"data": tool}]}},
        }
        yield {
            "kind": "status-update",
            "status": {"state": "TASK_STATE_WORKING", "message": {"parts": [{"text": answer[:3]}]}},
        }
        yield {"kind": "artifact-update", "artifact": {"parts": [{"text": answer}]}}
        yield {"kind": "status-update", "status": {"state": "TASK_STATE_COMPLETED"}, "final": True}

    return stream


async def _no_task(agent, context_id):
    """`latest_task` for a context the agent has never seen."""
    return None


def test_parse_agents_slugs_ids_and_skips_entries_without_a_url():
    parsed = ra.parse_agents(
        [
            {"label": "Order Tracker", "url": "http://a/a2a/1"},
            {"label": "no url"},
            "not a dict",
            {"id": "it-desk", "label": "IT", "url": "http://a/a2a/2", "token": "t"},
        ]
    )
    assert [p.id for p in parsed] == ["order_tracker", "it_desk"]
    assert parsed[1].request_headers() == {"x-api-key": "t"}


def test_an_a2a_endpoint_maps_onto_its_agent_card():
    assert (
        ra._card_url("http://h:2024/a2a/abc")
        == "http://h:2024/.well-known/agent-card.json?assistant_id=abc"
    )
    card = "http://h/.well-known/agent-card.json?assistant_id=abc"
    assert ra._card_url(card) == card


def test_every_tool_receives_its_runtime():
    tools = [rs.dynamic_task_tool(_static_task(), [AGENT]), *rs.background_tools([AGENT])]
    for tool in tools:
        assert "runtime" in tool._injected_args_keys, tool.name


def test_dynamic_task_lists_remote_agents_and_forwards_built_ins(monkeypatch):
    static = _static_task()
    dynamic = rs.dynamic_task_tool(static, [AGENT])
    assert "general-purpose" in dynamic.description
    assert "order_tracker" in dynamic.description

    monkeypatch.setattr(ra, "stream_message", _fake_stream("shipped"))
    monkeypatch.setattr(ra, "current_task", _no_task)
    frames: list[dict] = []
    runtime = SimpleNamespace(stream_writer=frames.append, tool_call_id="call-1", config={})
    coroutine = dynamic.coroutine
    assert coroutine is not None

    async def call(subagent_type: str) -> object:
        return await coroutine(description="where", subagent_type=subagent_type, runtime=runtime)

    remote = asyncio.run(call("order_tracker"))
    built_in = asyncio.run(call("general-purpose"))
    assert remote == "shipped"
    assert built_in == "static:general-purpose"
    assert static.metadata == {"calls": ["general-purpose"]}
    assert [f["kind"] for f in frames] == ["task", "step", "text", "answer", "state"]
    assert {f["call_id"] for f in frames} == {"call-1"}
    assert frames[1]["text"] == "read /data/orders.csv"


def test_tool_hook_gives_interpreter_code_the_dynamic_task(monkeypatch):
    static = _static_task()
    runtime = SimpleNamespace(context=None, tools=[static])

    @dataclasses.dataclass
    class Request:
        tool_call: dict
        tool: object
        runtime: object

    async def fake_load(self, runtime):
        return [AGENT], {}

    monkeypatch.setattr(rs.RemoteAgents, "_load", fake_load)
    monkeypatch.setattr(dataclasses, "replace", _replace_any)
    seen = {}

    async def handler(request):
        seen["tools"] = request.runtime.tools
        return "ok"

    request = Request(tool_call={"name": "eval"}, tool=None, runtime=runtime)
    asyncio.run(rs.RemoteAgents().awrap_tool_call(request, handler))
    (task,) = seen["tools"]
    assert task is not static
    assert "order_tracker" in task.description


_real_replace = dataclasses.replace


def _replace_any(obj, **changes):
    """`dataclasses.replace` that also accepts the SimpleNamespace runtime stand-in."""
    if isinstance(obj, SimpleNamespace):
        return SimpleNamespace(**{**vars(obj), **changes})

    return _real_replace(obj, **changes)


def test_a_finished_background_task_wakes_its_parent_thread(monkeypatch):
    notified: list[ra.BackgroundTask] = []

    async def fake_notify(task):
        notified.append(task)

    monkeypatch.setattr(ra, "stream_message", _fake_stream("known issue"))
    monkeypatch.setattr(ra, "_notify", fake_notify)

    async def run() -> ra.BackgroundTask:
        task = ra.start_background(AGENT, "investigate", "thread-1", "assistant-1")
        assert task.job is not None
        await task.job
        return task

    task = asyncio.run(run())
    assert notified == [task]
    text = ra.notification_text(task)
    assert text.startswith(ra.NOTIFICATION_PREFIX)
    assert "status: completed" in text
    assert "known issue" in text
    assert task.remote_task_id == "ctx:run"
    assert task.events[-1] == {"kind": "state", "state": "completed", "final": True}


def test_a_failed_background_task_still_reports_back(monkeypatch):
    notified: list[ra.BackgroundTask] = []

    async def boom(agent, text, context_id, trace=None, task_id=None, resume=None):
        raise ra.RemoteAgentError("Order and Supply Tracker: overloaded")
        yield {}  # an async generator, like the real stream

    async def fake_notify(task):
        notified.append(task)

    monkeypatch.setattr(ra, "stream_message", boom)
    monkeypatch.setattr(ra, "_notify", fake_notify)

    async def run() -> None:
        task = ra.start_background(AGENT, "x", "thread-1", "assistant-1")
        assert task.job is not None
        await task.job

    asyncio.run(run())
    (task,) = notified
    assert "status: failed" in ra.notification_text(task)


@pytest.mark.parametrize(
    ("task", "expected"),
    [
        ({"artifacts": [{"parts": [{"text": "a"}]}]}, "a"),
        ({"status": {"message": {"parts": [{"text": "b"}]}}}, "b"),
        (
            {
                "history": [
                    {"role": "ROLE_USER", "parts": [{"text": "q"}]},
                    {"role": "ROLE_AGENT", "parts": [{"text": "c"}]},
                ]
            },
            "c",
        ),
        ({}, ""),
    ],
)
def test_task_text_reads_every_place_an_answer_can_be(task, expected):
    assert ra.task_text(task) == expected


def test_trace_headers_round_trip_into_a_parent_in_the_callers_project():
    span = RunTree(name="task", run_type="tool", session_name="HPE Agent Hub")
    with tracing_context(parent=span):
        headers = ra.trace_headers()

    # Agent Server splits baggage into its own configurable keys.
    configurable = {"langsmith-trace": headers["langsmith-trace"]}
    for item in headers["baggage"].split(","):
        key, _, value = item.partition("=")
        configurable[key] = value

    parent = ra.remote_parent(configurable)
    assert parent is not None
    assert str(parent.id) == str(span.id)
    assert parent.session_name == "HPE Agent Hub"


def test_an_unmarked_trace_parent_is_ignored():
    """Voice mode sends `langsmith-trace` with no marker; its runs must keep their own roots."""
    header = RunTree(name="voice_tool").to_headers()["langsmith-trace"]
    assert ra.remote_parent({"langsmith-trace": header}) is None
    assert (
        ra.remote_parent({"langsmith-trace": header, "langsmith-metadata": '{"other": 1}'}) is None
    )
    assert ra.trace_headers() == {}


class _FakeAgentServer:
    """An A2A agent whose graph asks a question, then wants an approval, then answers.

    Plays the events `langgraph_api` emits (an `Interrupt` data artifact, then a final
    `input-required` status) and records every message it is sent.
    """

    QUESTION = {"kind": "user_question", "question": "Which order?", "options": ["A-1", "B-2"]}
    APPROVAL = {
        "action_requests": [{"name": "ship_order", "arguments": {}, "description": "Ship it?"}],
        "review_configs": [{"action_name": "ship_order", "allowed_decisions": ["approve"]}],
    }

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def stream(self, agent, text, context_id, trace=None, task_id=None, resume=None):
        self.sent.append(
            {"text": text, "context": context_id, "task_id": task_id, "resume": resume}
        )
        rounds = len(self.sent)
        yield {"task": {"id": "remote-1", "status": {"state": "TASK_STATE_SUBMITTED"}}}
        if rounds < 3:
            value = self.QUESTION if rounds == 1 else self.APPROVAL
            yield {
                "taskId": "remote-1",
                "kind": "artifact-update",
                "artifact": {
                    "name": "Interrupt",
                    "parts": [{"data": {"id": f"i{rounds}", "value": value}}],
                },
            }
            yield {
                "taskId": "remote-1",
                "kind": "status-update",
                "status": {"state": "TASK_STATE_INPUT_REQUIRED"},
                "final": True,
            }
            return

        yield {"kind": "artifact-update", "artifact": {"parts": [{"text": "B-2 shipped"}]}}
        yield {"kind": "status-update", "status": {"state": "TASK_STATE_COMPLETED"}, "final": True}


class _ParentState(BaseModel):
    """The parent graph's state: just its messages."""

    messages: Annotated[list, add_messages] = Field(default_factory=list)


def _parent_graph(node):
    """A checkpointed one-node parent graph, so `interrupt` and resume behave as in a run."""
    graph = StateGraph(_ParentState)
    graph.add_node("tools", node)
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    return graph.compile(checkpointer=InMemorySaver())


def _graph_runtime(call_id: str) -> SimpleNamespace:
    """A tool runtime carrying the running graph's config, as `ToolNode` builds one."""
    return SimpleNamespace(stream_writer=None, tool_call_id=call_id, config=get_config(), state={})


def test_the_waiting_tasks_question_and_options_are_read_off_the_interrupt(monkeypatch):
    server = _FakeAgentServer()
    monkeypatch.setattr(ra, "stream_message", server.stream)
    task = asyncio.run(ra.run_streaming(AGENT, "x", "ctx", lambda item: None))
    assert ra.needs_input(task)
    assert task["id"] == "remote-1"
    assert ra.input_request(task) == ("Which order?", ["A-1", "B-2"])
    assert ra.pending_interrupt(task) == _FakeAgentServer.QUESTION


def test_a_plain_status_message_is_the_question_when_there_is_no_interrupt_payload():
    task = {
        "status": {
            "state": "TASK_STATE_AUTH_REQUIRED",
            "message": {"parts": [{"text": "Sign in at https://idp.test"}]},
        }
    }
    assert ra.needs_input(task)
    assert ra.input_request(task) == ("Sign in at https://idp.test", [])
    payload = rs.interrupt_payload(AGENT, task)
    assert payload["kind"] == "user_question"
    assert "authenticate" in payload["question"]


def test_every_remote_question_reaches_the_human_and_each_answer_is_sent_once(monkeypatch):
    # The case a plain function gets wrong: two questions in one `task` call. Each
    # resume must send only the newest answer, never replay the first into the second.
    server = _FakeAgentServer()
    monkeypatch.setattr(ra, "stream_message", server.stream)
    monkeypatch.setattr(ra, "current_task", _no_task)
    dynamic = rs.dynamic_task_tool(_static_task(), [AGENT])
    coroutine = dynamic.coroutine
    assert coroutine is not None

    async def tools(state):
        out = await coroutine(
            description="ship it", subagent_type="order_tracker", runtime=_graph_runtime("call-7")
        )
        return {"messages": [("ai", str(out))]}

    graph = _parent_graph(tools)
    config = {"configurable": {"thread_id": "t-sync"}}

    async def run():
        first = await graph.ainvoke({"messages": [("user", "go")]}, config)
        second = await graph.ainvoke(Command(resume={"answer": "B-2"}), config)
        approve = {"decisions": [{"type": "approve"}]}
        final = await graph.ainvoke(Command(resume=approve), config)
        return first, second, final

    first, second, final = asyncio.run(run())
    asked = first["__interrupt__"][0].value
    assert asked["kind"] == "user_question"
    assert asked["options"] == ["A-1", "B-2"]
    # The approval reaches the human as an approval, not as a question about one.
    assert second["__interrupt__"][0].value["action_requests"][0]["name"] == "ship_order"
    assert final["messages"][-1].content == "B-2 shipped"

    request, answer, decision = server.sent
    assert request["text"] == "ship it" and request["task_id"] is None
    assert answer["resume"] == {"answer": "B-2"} and answer["task_id"] == "remote-1"
    assert decision["resume"] == {"decisions": [{"type": "approve"}]}
    assert {m["context"] for m in server.sent} == {request["context"]}


def test_a_waiting_background_task_is_answered_only_by_the_human(monkeypatch):
    server = _FakeAgentServer()
    notes: list[str] = []

    async def fake_notify(task):
        notes.append(ra.notification_text(task))

    monkeypatch.setattr(ra, "stream_message", server.stream)
    monkeypatch.setattr(ra, "_notify", fake_notify)
    tools = {t.name: t for t in rs.background_tools([AGENT]) if isinstance(t, StructuredTool)}
    ask = tools["ask_user_for_remote_task"].coroutine
    update = tools["update_remote_task"].coroutine
    assert ask is not None and update is not None
    held: dict[str, str] = {}

    async def node(state):
        out = await ask(task_id=held["id"], runtime=_graph_runtime("call-ask"))
        return {"messages": [("ai", out.update["messages"][0].content)]}

    graph = _parent_graph(node)

    async def run():
        task = ra.start_background(AGENT, "ship it", "thread-1", "assistant-1")
        held["id"] = task.task_id
        assert task.job is not None
        await task.job
        refusal = await update(
            task_id=task.task_id, message="B-2", runtime=SimpleNamespace(state={}, config={})
        )
        config = {"configurable": {"thread_id": "t-bg"}}
        paused = await graph.ainvoke({"messages": [("user", "x")]}, config)
        await graph.ainvoke(Command(resume={"answer": "B-2"}), config)
        await task.job
        return task, refusal, paused

    task, refusal, paused = asyncio.run(run())
    assert "status: input_required" in notes[0]
    assert f"ask_user_for_remote_task(task_id={task.task_id!r})" in notes[0]
    assert "waiting for the user" in refusal
    assert (
        paused["__interrupt__"][0].value["question"]
        == "Order and Supply Tracker asks: Which order?"
    )
    assert len(server.sent) == 2
    assert server.sent[1]["resume"] == {"answer": "B-2"}
    assert server.sent[1]["task_id"] == "remote-1"


def test_the_current_task_takes_its_state_from_get_task(monkeypatch):
    # Agent Server 0.13.0 lists an interrupted task as completed; only GetTask, which
    # checks the thread, says it is waiting. The listed task's artifacts are kept.
    listed = {
        "id": "remote-1",
        "status": {"state": "TASK_STATE_COMPLETED"},
        "artifacts": [{"parts": [{"data": {"value": "Which order?"}}]}],
    }
    calls: list[str] = []

    async def rpc(agent, method, params, timeout):
        calls.append(method)
        if method == "ListTasks":
            return {"tasks": [listed]}

        return {"id": "remote-1", "status": {"state": "TASK_STATE_INPUT_REQUIRED"}}

    monkeypatch.setattr(ra, "_rpc", rpc)
    task = asyncio.run(ra.current_task(AGENT, "ctx"))
    assert task is not None
    assert calls == ["ListTasks", "GetTask"]
    assert ra.needs_input(task)
    assert ra.input_request(task) == ("Which order?", [])
