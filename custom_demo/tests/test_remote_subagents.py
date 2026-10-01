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

    async def stream(agent, text, context_id, trace=None, task_id=None):
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


def _asking_stream(question: str, options: list[str], sent: list[dict]):
    """A `stream_message` stand-in for an Agent Server whose graph interrupts.

    It plays the events `langgraph_api` emits for an interrupt (an `Interrupt` data
    artifact, then a final `input-required` status carrying the prompt), and records
    every message it is sent.
    """

    async def stream(agent, text, context_id, trace=None, task_id=None):
        sent.append({"text": text, "context": context_id, "task_id": task_id})
        yield {"task": {"id": "remote-1", "status": {"state": "TASK_STATE_SUBMITTED"}}}
        value = {"kind": "user_question", "question": question, "options": options}
        yield {
            "taskId": "remote-1",
            "kind": "artifact-update",
            "artifact": {"name": "Interrupt", "parts": [{"data": {"id": "i1", "value": value}}]},
        }
        yield {
            "taskId": "remote-1",
            "kind": "status-update",
            "status": {
                "state": "TASK_STATE_INPUT_REQUIRED",
                "message": {"role": "ROLE_AGENT", "parts": [{"text": question}]},
            },
            "final": True,
        }

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

    async def boom(agent, text, context_id, trace=None, task_id=None):
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


class _Paused(Exception):
    """Stands in for the `GraphInterrupt` that `interrupt` raises inside a real run."""


def _remote_call(dynamic, call_id="call-7"):
    """Call the dynamic `task` once, on a runtime shaped like a graph's."""
    runtime = SimpleNamespace(
        stream_writer=lambda frame: None,
        tool_call_id=call_id,
        config={"configurable": {"thread_id": "thread-1"}, "metadata": {"assistant_id": "a-1"}},
        state={},
    )
    coroutine = dynamic.coroutine
    assert coroutine is not None
    return asyncio.run(
        coroutine(description="where is it", subagent_type="order_tracker", runtime=runtime)
    )


def test_the_waiting_tasks_question_and_options_are_read_off_the_interrupt():
    sent: list[dict] = []

    async def run():
        return await ra.run_streaming(AGENT, "x", "ctx", lambda item: None, None)

    stream = _asking_stream("Which order?", ["A-1", "B-2"], sent)
    task = asyncio.run(_with_stream(stream, run))
    assert ra.needs_input(task)
    assert task["id"] == "remote-1"
    assert ra.input_request(task) == ("Which order?", ["A-1", "B-2"])


def test_a_plain_status_message_is_the_question_when_there_is_no_interrupt_payload():
    task = {
        "status": {
            "state": "TASK_STATE_AUTH_REQUIRED",
            "message": {"parts": [{"text": "Sign in at https://idp.test"}]},
        }
    }
    assert ra.needs_input(task)
    assert ra.input_request(task) == ("Sign in at https://idp.test", [])


async def _with_stream(stream, run):
    original = ra.stream_message
    ra.stream_message = stream
    try:
        return await run()
    finally:
        ra.stream_message = original


def test_a_remote_question_pauses_the_parent_and_the_answer_resumes_the_same_task(monkeypatch):
    sent: list[dict] = []
    stream = _asking_stream("Which order?", ["A-1", "B-2"], sent)
    remote: dict[str, dict] = {}

    async def latest_task(agent, context_id):
        return remote.get(context_id)

    async def record(agent, text, context_id, trace=None, task_id=None):
        # First message: the agent asks. The answer: the agent finishes.
        if task_id is None:
            async for event in stream(agent, text, context_id, trace, task_id):
                yield event

            remote[context_id] = {
                "id": "remote-1",
                "status": {"state": "TASK_STATE_INPUT_REQUIRED"},
                "artifacts": [
                    {
                        "parts": [
                            {
                                "data": {
                                    "value": {"question": "Which order?", "options": ["A-1", "B-2"]}
                                }
                            }
                        ]
                    }
                ],
            }
            return

        sent.append({"text": text, "context": context_id, "task_id": task_id})
        yield {"kind": "artifact-update", "artifact": {"parts": [{"text": "A-1 ships Friday"}]}}
        yield {"kind": "status-update", "status": {"state": "TASK_STATE_COMPLETED"}, "final": True}

    asked: list[dict] = []
    resume: list[object] = []

    def fake_interrupt(payload):
        asked.append(payload)
        if not resume:
            raise _Paused

        return resume[0]

    monkeypatch.setattr(ra, "stream_message", record)
    monkeypatch.setattr(ra, "current_task", latest_task)
    monkeypatch.setattr(rs, "interrupt", fake_interrupt)
    dynamic = rs.dynamic_task_tool(_static_task(), [AGENT])

    with pytest.raises(_Paused):
        _remote_call(dynamic)

    assert asked[0]["kind"] == "user_question"
    assert asked[0]["options"] == ["A-1", "B-2"]
    assert "Which order?" in asked[0]["question"]

    # The resume re-runs the tool from the top, as LangGraph does.
    resume.append({"answer": "A-1"})
    assert _remote_call(dynamic) == "A-1 ships Friday"
    first, answer = sent
    assert answer == {"text": "A-1", "context": first["context"], "task_id": "remote-1"}
    assert [m["text"] for m in sent].count("where is it") == 1


def test_a_second_question_in_one_call_is_held_for_update_remote_task(monkeypatch):
    sent: list[dict] = []
    stream = _asking_stream("And which site?", [], sent)
    waiting = {
        "id": "remote-1",
        "status": {"state": "TASK_STATE_INPUT_REQUIRED"},
        "artifacts": [{"parts": [{"data": {"value": "Which order?"}}]}],
    }

    async def latest_task(agent, context_id):
        return waiting

    monkeypatch.setattr(ra, "stream_message", stream)
    monkeypatch.setattr(ra, "current_task", latest_task)
    monkeypatch.setattr(rs, "interrupt", lambda payload: {"answer": "A-1"})
    dynamic = rs.dynamic_task_tool(_static_task(), [AGENT])

    result = _remote_call(dynamic, call_id="call-9")
    message = result.update["messages"][0].content
    assert "And which site?" in message
    assert "update_remote_task" in message
    (task_id,) = result.update["remote_tasks"]
    held = ra.get_task(task_id)
    assert held is not None and held.remote_task_id == "remote-1"
    assert sent == [{"text": "A-1", "context": task_id, "task_id": "remote-1"}]


def test_answering_a_waiting_background_task_resumes_its_remote_task(monkeypatch):
    sent: list[dict] = []
    notes: list[str] = []
    asking = _asking_stream("Which order?", [], sent)
    finishing = _fake_stream("A-1 ships Friday")

    async def stream(agent, text, context_id, trace=None, task_id=None):
        play = finishing if task_id else asking
        if task_id:
            sent.append({"text": text, "context": context_id, "task_id": task_id})

        async for event in play(agent, text, context_id, trace, task_id):
            yield event

    async def fake_notify(task):
        notes.append(ra.notification_text(task))

    monkeypatch.setattr(ra, "stream_message", stream)
    monkeypatch.setattr(ra, "_notify", fake_notify)

    async def run() -> ra.BackgroundTask:
        task = ra.start_background(AGENT, "investigate", "thread-1", "assistant-1")
        assert task.job is not None
        await task.job
        ra.restart_background(task, "A-1")
        await task.job
        return task

    task = asyncio.run(run())
    waiting, finished = notes
    assert "status: input_required" in waiting
    assert "Which order?" in waiting
    assert f"update_remote_task(task_id={task.task_id!r}" in waiting
    assert sent[-1] == {"text": "A-1", "context": task.task_id, "task_id": "remote-1"}
    assert "status: completed" in finished
    assert "A-1 ships Friday" in finished


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
