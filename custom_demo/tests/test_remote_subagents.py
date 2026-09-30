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

import pytest
from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool
from langsmith import tracing_context
from langsmith.run_trees import RunTree

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

    async def stream(agent, text, context_id, trace=None):
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
    frames: list[dict] = []
    runtime = SimpleNamespace(stream_writer=frames.append, tool_call_id="call-1")
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

    async def boom(agent, text, context_id, trace=None):
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
