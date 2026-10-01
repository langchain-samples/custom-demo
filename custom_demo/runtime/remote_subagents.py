"""Remote A2A agents as subagents, decided per run instead of at graph build.

`create_deep_agent(subagents=...)` fixes the subagent list when the graph is built,
and the `task` tool it makes is a closure over that list. Which remote agents an
assistant has is per-assistant configuration, so `RemoteAgents` does for subagents
what `McpTools` does for tools:

  * `awrap_model_call` replaces the built-in `task` tool in `request.tools` with one
    that lists the built-in subagents AND this run's remote agents, and adds the six
    background-task tools. This is what the model sees.
  * `awrap_tool_call` hands `ToolNode` the same dynamic tools at execution time, since
    `ToolNode` only knows the tools it was built with. It also swaps the dynamic
    `task` into `runtime.tools`, which is where interpreter code's `task()` global
    looks it up, so remote agents are dispatchable from code as well.

Built-in subagents run unchanged: the dynamic `task` forwards any `subagent_type` it
does not own to the original tool.

A remote agent that stops for input (A2A `input-required` or `auth-required`) pauses
the parent run, the way a local subagent's `interrupt()` does: the human sees the
remote agent's own interrupt payload and their answer goes back to it unchanged. The
model never answers for them. `task` runs the exchange as a two-node graph (send,
then ask) for the same reason deepagents' `task` runs its subagent as a graph: each
round is a checkpointed step in the parent's namespace, so a resume re-runs only the
paused `ask` and the remote agent can ask any number of times. A plain function
would re-run from the top on every resume and replay earlier answers into later
questions.

A background task that stops for input is put to the human by
`ask_user_for_remote_task`, one interrupt per call; `update_remote_task` refuses it.

Async only, like `McpTools`: a sync run (evals, unit tests) sees no remote agents.
"""

from __future__ import annotations

import contextvars
import dataclasses
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, NotRequired

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field

from custom_demo.core.ctx import get_ctx
from custom_demo.runtime import remote_agents as ra

TASK_TOOL = "task"

# The `type` of the custom stream frames a remote `task` call emits as it progresses;
# frontend/src/components/ChatPanel.tsx draws them as a subagent card.
REMOTE_PROGRESS = "remote_agent_progress"

# This run's remote agents and the ones that could not be reached, for the prompt
# note. None means discovery did not run on this path (the sync path), which is not
# the same as "discovery ran and found nothing".
remote_agents_seen: contextvars.ContextVar[tuple[list[ra.RemoteAgent], dict[str, str]] | None] = (
    contextvars.ContextVar("remote_agents_seen", default=None)
)


def _tasks_reducer(existing: dict[str, dict] | None, update: dict[str, dict]) -> dict[str, dict]:
    """Merge task records by id."""
    return {**(existing or {}), **update}


class RemoteTasksState(AgentState):
    """The background tasks a thread started, kept out of the message history.

    Compaction summarises old messages; a task id that lived only in a tool message
    would be summarised away while the task was still running. This channel keeps
    every task the thread started, so `list_remote_tasks` can always find it.
    """

    remote_tasks: Annotated[NotRequired[dict[str, dict]], _tasks_reducer]


# --------------------------------------------------------------------------- #
# The dynamic `task` tool                                                       #
# --------------------------------------------------------------------------- #


class _TaskArgs(BaseModel):
    """Input schema for `task`, identical to the built-in one."""

    description: str = Field(
        description=(
            "A detailed description of the task for the subagent to perform autonomously. "
            "Include all necessary context and specify the expected output format."
        )
    )
    subagent_type: str = Field(
        description="The type of subagent to use. Must be one of the available agent types listed in the tool description."
    )


def _remote_listing(agents: list[ra.RemoteAgent]) -> str:
    """The remote agents, in the `- name: description` form the task tool lists agents in."""
    return "\n".join(f"- {a.name}: {a.description} (remote A2A agent: {a.label})" for a in agents)


def dynamic_task_tool(static: BaseTool, agents: list[ra.RemoteAgent]) -> StructuredTool:
    """A `task` tool that knows this run's remote agents and forwards the rest to `static`."""
    by_name = {a.name: a for a in agents}
    description = (
        f"{static.description}\n\nRemote agents, reached over A2A. They see only the "
        f"description you send and cannot read your files:\n{_remote_listing(agents)}"
    )
    static_func = getattr(static, "func", None)
    static_coroutine = getattr(static, "coroutine", None)

    def task(description: str, subagent_type: str, runtime: ToolRuntime) -> str | Command:
        if subagent_type in by_name:
            return f"Remote agent {subagent_type} can only be called on the async runtime."

        assert static_func is not None
        return static_func(description=description, subagent_type=subagent_type, runtime=runtime)

    async def atask(description: str, subagent_type: str, runtime: ToolRuntime) -> str | Command:
        agent = by_name.get(subagent_type)
        if agent is None:
            assert static_coroutine is not None
            return await static_coroutine(
                description=description, subagent_type=subagent_type, runtime=runtime
            )

        # Streamed rather than sent, so the chat can draw the remote agent's steps and its
        # answer forming while this call blocks, the way it draws a local subagent's.
        write = getattr(runtime, "stream_writer", None)
        call_id = getattr(runtime, "tool_call_id", None)

        def report(item: dict) -> None:
            if write is not None:
                write(
                    {
                        "type": REMOTE_PROGRESS,
                        "call_id": call_id,
                        "agent": agent.name,
                        "label": agent.label,
                        **item,
                    }
                )

        exchange = _exchange_graph(agent, report)
        try:
            final = await exchange.ainvoke(
                {"context": _call_context(runtime), "request": description, "answering": False}
            )
        except GraphBubbleUp:
            # The `ask` node's interrupt, pausing the parent run; not a failure.
            raise
        except Exception as exc:  # noqa: BLE001 - the model is told the agent failed
            report({"kind": "state", "state": "failed", "final": True})
            return f"Remote agent {agent.name} failed: {type(exc).__name__}: {exc}"

        answer = final["task"]
        text = ra.task_text(answer)
        state = ra.task_state(answer)
        return text if state == "completed" and text else f"[{agent.name} {state}] {text}"

    return StructuredTool.from_function(
        name=TASK_TOOL,
        func=task,
        coroutine=atask,
        description=description,
        infer_schema=False,
        args_schema=_TaskArgs,
    )


def _call_context(runtime: ToolRuntime) -> str:
    """The A2A context for one `task` call: one remote conversation per dispatch.

    Derived from the thread and the tool call rather than random, so the re-run that
    follows an interrupt reaches the same remote conversation.
    """
    thread, _ = _parent(runtime)
    call = getattr(runtime, "tool_call_id", None) or uuid.uuid4().hex
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"remote-task:{thread}:{call}"))


def interrupt_payload(agent: ra.RemoteAgent, task: dict) -> dict:
    """What the human is shown for a remote agent that is waiting for input.

    The remote graph's own `interrupt()` value when it sent a structured one, so an
    approval stays an approval; otherwise a `user_question` built from what it said,
    which the chat draws as a question card (a text box when there are no options).
    """
    value = ra.pending_interrupt(task)
    if isinstance(value, dict):
        payload = {**value, "remote_agent": agent.name}
        if payload.get("kind") == "user_question" and payload.get("question"):
            payload["question"] = f"{agent.label} asks: {payload['question']}"

        return payload

    question, options = ra.input_request(task)
    if ra.task_state(task) == "auth_required":
        question = (
            f"{question}\n\n({agent.label} needs you to authenticate before it can continue.)"
        )

    return {
        "kind": "user_question",
        "question": f"{agent.label} asks: {question}",
        "options": options,
        "remote_agent": agent.name,
    }


class _ExchangeState(BaseModel):
    """One `task` call's conversation with a remote agent, checkpointed per round."""

    context: str
    request: str
    answering: bool = False
    task: dict = Field(default_factory=dict)
    reply: Any = None


def _exchange_graph(agent: ra.RemoteAgent, report: Callable[[dict], None]):
    """Send the request, then ask the human each time the remote agent waits for input.

    Compiled without a checkpointer, so it saves into the parent's when there is one:
    invoked inside the parent's tool call, it checkpoints under that call's namespace,
    and invoking it again on resume continues from the paused `ask` node.
    """

    async def send(state: _ExchangeState) -> dict:
        context = state.context
        if not state.answering:
            # A re-run after a crash mid-stream finds the task this call already
            # started, by its context, instead of sending the request again.
            task = await ra.current_task(agent, context) or await ra.run_streaming(
                agent, state.request, context, report, ra.trace_headers()
            )
            return {"task": task}

        task = await ra.run_streaming(
            agent,
            ra.answer_text(state.reply),
            context,
            report,
            ra.trace_headers(),
            state.task.get("id"),
            state.reply,
        )
        return {"task": task, "answering": False}

    def ask(state: _ExchangeState) -> dict:
        return {"reply": interrupt(interrupt_payload(agent, state.task)), "answering": True}

    def route(state: _ExchangeState) -> str:
        return "ask" if ra.needs_input(state.task) else END

    graph = StateGraph(_ExchangeState)
    graph.add_node("send", send)
    graph.add_node("ask", ask)
    graph.add_edge(START, "send")
    graph.add_conditional_edges("send", route, ["ask", END])
    graph.add_edge("ask", "send")
    return graph.compile(name=f"remote:{agent.name}")


# --------------------------------------------------------------------------- #
# Background-task tools                                                         #
# --------------------------------------------------------------------------- #


def _parent(runtime: ToolRuntime) -> tuple[str | None, str | None]:
    """The thread and assistant this run belongs to, which a finished task wakes."""
    config = runtime.config or {}
    configurable = config.get("configurable") or {}
    metadata = config.get("metadata") or {}
    thread = configurable.get("thread_id") or metadata.get("thread_id")
    assistant = metadata.get("assistant_id") or configurable.get("assistant_id")
    return (str(thread) if thread else None, str(assistant) if assistant else None)


def _record(task: ra.BackgroundTask, status: str) -> dict:
    """What the state channel keeps about one task."""
    return {
        "task_id": task.task_id,
        "agent": task.agent.name,
        "description": task.description[:300],
        "status": status,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(task.started)),
    }


def _recorded(task: ra.BackgroundTask, status: str, note: str, runtime: ToolRuntime) -> Command:
    """Answer a tool call with `note` and record the task's new status on the thread."""
    return Command(
        update={
            "remote_tasks": {task.task_id: _record(task, status)},
            "messages": [ToolMessage(note, tool_call_id=runtime.tool_call_id or "")],
        }
    )


async def _cancel_remote(task: ra.BackgroundTask) -> None:
    """Ask the remote agent to stop its run of the task, if that run is still going."""
    remote = await ra.latest_task(task.agent, task.task_id)
    if remote and ra.task_state(remote) in ("working", "submitted"):
        await ra.cancel_task(task.agent, str(remote["id"]))


def _live_status(task: ra.BackgroundTask) -> str:
    """Where a task this process holds has got to."""
    if task.cancelled:
        return "cancelled"

    if task.error:
        return "failed"

    if task.result is not None:
        return ra.task_state(task.result)

    return "working"


def _held_report(task: ra.BackgroundTask) -> str:
    """What `check_remote_task` says about a task this process holds."""
    status = _live_status(task)
    if status == "working":
        return f"{task.agent.name} task {task.task_id}: still working ({round(time.time() - task.started)}s)."

    if task.error:
        return f"{task.agent.name} task {task.task_id}: failed: {task.error}"

    if task.result and ra.needs_input(task.result):
        return f"{task.agent.name} task {task.task_id}: {status}\n\n{ra.waiting_note(task)}"

    return f"{task.agent.name} task {task.task_id}: {status}\n\n{ra.task_text(task.result or {})}"


class _StartArgs(BaseModel):
    """Input schema for `start_remote_task`."""

    agent: str = Field(description="Remote agent name, from the task tool's remote agent list.")
    description: str = Field(
        description="The complete request for that agent. It sees nothing else, so include every fact it needs."
    )


class _TaskIdArgs(BaseModel):
    """Input schema for tools that take one task id."""

    task_id: str = Field(description="The full task_id `start_remote_task` returned.")


class _UpdateArgs(_TaskIdArgs):
    """Input schema for `update_remote_task`."""

    message: str = Field(description="New instructions; the agent restarts with them.")


class _NoArgs(BaseModel):
    """No input."""


def _sync_unavailable(runtime: ToolRuntime, **_: Any) -> str:
    """Background tasks need the async runtime; say so rather than fail.

    `runtime` is in the signature on purpose: LangChain decides whether to inject the
    tool runtime by inspecting the SYNC function, so a stub without it leaves the async
    coroutine called with no runtime at all.
    """
    return "Remote background tasks are only available on the async runtime."


def background_tools(agents: list[ra.RemoteAgent]) -> list[BaseTool]:
    """The six tools that start, follow, redirect, answer and stop background remote tasks."""
    by_name = {a.name: a for a in agents}

    async def start(agent: str, description: str, runtime: ToolRuntime) -> str | Command:
        target = by_name.get(agent.strip())
        if target is None:
            return (
                f"No remote agent {agent!r}. Remote agents: {', '.join(sorted(by_name)) or 'none'}."
            )

        thread, assistant = _parent(runtime)
        task = ra.start_background(target, description, thread, assistant)
        # frontend/src/components/ChatPanel.tsx reads `task_id=<id> on <agent>.` out of
        # this text to follow the task live: a wire-format dependency, keep them in step.
        note = (
            f"Started task_id={task.task_id} on {target.name}. It runs in the background. When it "
            f"finishes you will receive a message starting with '{ra.NOTIFICATION_PREFIX}' "
            "carrying its answer, so do not poll for it: carry on, or end your turn."
        )
        return _recorded(task, "working", note, runtime)

    async def check(task_id: str, runtime: ToolRuntime) -> str:
        task = ra.get_task(task_id)
        if task is not None:
            return _held_report(task)

        # Not held by this process (a restart, or another worker): ask the agent.
        record = (runtime.state.get("remote_tasks") or {}).get(task_id.strip())
        agent = by_name.get(record["agent"]) if record else None
        if agent is None:
            return f"No remote task {task_id!r} on this thread."

        remote = await ra.current_task(agent, task_id.strip())
        if remote is None:
            return f"{agent.name} has no record of task {task_id}."

        if ra.needs_input(remote):
            question, _ = ra.input_request(remote)
            return (
                f"{agent.name} task {task_id}: {ra.task_state(remote)}\n\n{question}\n\n"
                f"Call ask_user_for_remote_task(task_id={task_id.strip()!r}) to put it to the user."
            )

        return f"{agent.name} task {task_id}: {ra.task_state(remote)}\n\n{ra.task_text(remote)}"

    async def cancel(task_id: str, runtime: ToolRuntime) -> str | Command:
        task = ra.get_task(task_id)
        if task is None:
            return f"No remote task {task_id!r} held by this server."

        if task.job and not task.job.done():
            task.job.cancel()

        await _cancel_remote(task)
        task.cancelled = True
        return _recorded(task, "cancelled", f"Cancelled {task.task_id}.", runtime)

    async def update(task_id: str, message: str, runtime: ToolRuntime) -> str | Command:
        task = ra.get_task(task_id) or await _rehold_waiting(task_id, runtime)
        if task is None:
            return f"No remote task {task_id!r} held by this server."

        if task.result and ra.needs_input(task.result):
            return f"{task.task_id} is waiting for the user. {ra.waiting_note(task)}"

        # Cancel the run in flight, then continue the same remote conversation with the
        # new instructions, so the agent keeps what it already did.
        if task.job and not task.job.done():
            task.job.cancel()
            await _cancel_remote(task)

        ra.restart_background(task, message)
        note = f"Sent new instructions to {task.task_id}; you will be notified when it finishes."
        return _recorded(task, "working", note, runtime)

    async def ask_user(task_id: str, runtime: ToolRuntime) -> str | Command:
        task = ra.get_task(task_id) or await _rehold_waiting(task_id, runtime)
        if task is None or not (task.result and ra.needs_input(task.result)):
            return f"Remote task {task_id!r} is not waiting for input."

        # Nothing is sent before the interrupt, so the re-run on resume repeats no work:
        # it reaches here again and `interrupt` returns the human's answer.
        reply = interrupt(interrupt_payload(task.agent, task.result))
        ra.restart_background(task, ra.answer_text(reply), reply)
        note = f"Sent the user's answer to {task.task_id}; you will be notified when it finishes."
        return _recorded(task, "working", note, runtime)

    async def _rehold_waiting(task_id: str, runtime: ToolRuntime) -> ra.BackgroundTask | None:
        """A waiting task this process does not hold (a restart, another worker), held again."""
        record = (runtime.state.get("remote_tasks") or {}).get(task_id.strip())
        if record is None or record["agent"] not in by_name:
            return None

        agent = by_name[record["agent"]]
        remote = await ra.current_task(agent, task_id.strip())
        if remote is None or not ra.needs_input(remote):
            return None

        thread, assistant = _parent(runtime)
        return ra.hold_waiting(
            agent, task_id.strip(), record["description"], remote, thread, assistant
        )

    async def list_tasks(runtime: ToolRuntime) -> str:
        records = runtime.state.get("remote_tasks") or {}
        if not records:
            return "No remote tasks on this thread."

        lines = []
        for task_id, record in records.items():
            held = ra.get_task(task_id)
            status = _live_status(held) if held else record.get("status", "unknown")
            lines.append(f"- {task_id} {record['agent']}: {status}. {record['description'][:120]}")

        return "\n".join(lines)

    def tool(
        name: str, coroutine: Callable[..., Awaitable[Any]], schema: type[BaseModel], doc: str
    ) -> BaseTool:
        return StructuredTool.from_function(
            name=name,
            func=_sync_unavailable,
            coroutine=coroutine,
            description=doc,
            infer_schema=False,
            args_schema=schema,
        )

    return [
        tool(
            "start_remote_task",
            start,
            _StartArgs,
            "Start a remote agent on a request in the background and return at once. Use for "
            "work that takes minutes, or to run several agents at the same time. The result "
            "arrives by itself as a new message when the agent finishes.",
        ),
        tool(
            "check_remote_task",
            check,
            _TaskIdArgs,
            "Get a background remote task's status, and its answer if finished.",
        ),
        tool(
            "update_remote_task",
            update,
            _UpdateArgs,
            "Give a running background task new instructions; it restarts with them in the same conversation.",
        ),
        tool(
            "ask_user_for_remote_task",
            ask_user,
            _TaskIdArgs,
            "Put a waiting background task's question to the user and send the agent their "
            "answer. The only way to answer a task that is waiting for input.",
        ),
        tool("cancel_remote_task", cancel, _TaskIdArgs, "Stop a background remote task."),
        tool(
            "list_remote_tasks",
            list_tasks,
            _NoArgs,
            "List every background remote task this thread started, with live status.",
        ),
    ]


BACKGROUND_TOOL_NAMES = frozenset(
    {
        "start_remote_task",
        "check_remote_task",
        "update_remote_task",
        "ask_user_for_remote_task",
        "cancel_remote_task",
        "list_remote_tasks",
    }
)


# --------------------------------------------------------------------------- #
# Middleware                                                                    #
# --------------------------------------------------------------------------- #


class RemoteAgents(AgentMiddleware):
    """Offer the assistant's remote A2A agents as subagents for the duration of a run."""

    state_schema = RemoteTasksState

    async def _load(self, runtime: Any) -> tuple[list[ra.RemoteAgent], dict[str, str]]:
        configs = ra.parse_agents(get_ctx(runtime).remote_agents)
        return await ra.load_agents(configs) if configs else ([], {})

    def wrap_model_call(self, request, handler):
        """Pass through on the sync path; remote agents are async only (see module docstring)."""
        return handler(request)

    async def awrap_model_call(self, request, handler):
        """Swap in the dynamic `task` tool and add the background-task tools."""
        agents, down = await self._load(request.runtime)
        token = remote_agents_seen.set((agents, down))
        try:
            if agents:
                tools = []
                for t in request.tools:
                    is_task = isinstance(t, BaseTool) and t.name == TASK_TOOL
                    tools.append(dynamic_task_tool(t, agents) if is_task else t)

                request = request.override(tools=[*tools, *background_tools(agents)])

            return await handler(request)
        finally:
            remote_agents_seen.reset(token)

    def wrap_tool_call(self, request, handler):
        """Pass through on the sync path; also keeps dynamic-tool detection on."""
        return handler(request)

    async def awrap_tool_call(self, request, handler):
        """Hand `ToolNode` the dynamic tools, and put the dynamic `task` where `task()` looks."""
        agents, _ = await self._load(request.runtime)
        if not agents:
            return await handler(request)

        name = request.tool_call.get("name")
        if name == TASK_TOOL and isinstance(request.tool, BaseTool):
            request = dataclasses.replace(request, tool=dynamic_task_tool(request.tool, agents))
        elif name in BACKGROUND_TOOL_NAMES and request.tool is None:
            tool = next(t for t in background_tools(agents) if t.name == name)
            request = dataclasses.replace(request, tool=tool)

        # Interpreter code calls `task()`, which resolves the tool from `runtime.tools`:
        # the tools `ToolNode` was built with, so the static one unless replaced here.
        runtime = request.runtime
        built = list(getattr(runtime, "tools", None) or [])
        if built and any(getattr(t, "name", None) == TASK_TOOL for t in built):
            tools = [dynamic_task_tool(t, agents) if t.name == TASK_TOOL else t for t in built]
            request = dataclasses.replace(
                request, runtime=dataclasses.replace(runtime, tools=tools)
            )

        return await handler(request)


def remote_agents_note() -> str:
    """Tell the model which remote agents it has and how their results reach it."""
    seen = remote_agents_seen.get()
    if seen is None:
        return ""

    agents, down = seen
    parts = []
    if agents:
        names = ", ".join(a.name for a in agents)
        parts.append(
            f"\n\nREMOTE AGENTS ({names}). These are other teams' agents, reached over A2A and "
            "listed in the `task` tool beside your own subagents. Call them with `task` (also "
            "from interpreter code with `task()`) when you want the answer before you continue. "
            "For long work, or to run several at once while you keep talking to the user, use "
            "`start_remote_task`: it returns immediately, and when the agent finishes a message "
            f"starting with '{ra.NOTIFICATION_PREFIX}' arrives on this thread with its answer. "
            "That message comes from the system, not the user: act on the result (answer the "
            "user, or start the next step) without polling in between. A remote agent sees only "
            "what you send it, so put every fact it needs in the request."
        )

    if down:
        listed = "; ".join(f"{label} ({error})" for label, error in down.items())
        parts.append(
            f"\n\nREMOTE AGENTS UNAVAILABLE: {listed}. They are configured but could not be "
            "reached this turn. If a request needs one of them, say that it is unreachable."
        )

    return "".join(parts)
