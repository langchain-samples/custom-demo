"""Connect an assistant to remote A2A agents and run them as its subagents.

An assistant names its remote agents in `context.remote_agents`, the way it names MCP
servers in `context.mcp_servers`. This module reads each agent's A2A agent card and
turns it into two things the harness already knows how to use:

  * **A synchronous subagent.** It is added to the `task` tool the model sees, so
    `task(subagent_type="order_tracker", ...)` works from a tool call and from
    `task()` inside interpreter code, exactly like a built-in subagent. The call
    blocks until the remote agent answers.
  * **An asynchronous subagent.** `start_remote_task` returns at once and the remote
    agent works in the background. When it finishes, this process enqueues a run on
    the caller's own thread carrying the result (`multitask_strategy="enqueue"`), so an
    idle orchestrator is woken up and a busy one sees it as soon as it is free.
    Nothing is left for the orchestrator to remember to poll.

Why this lives here rather than in `create_deep_agent(subagents=...)`: which agents
exist is per-assistant configuration, and the subagent list is fixed when the graph
is built. `remote_subagents.py:RemoteAgents` rebuilds the `task` tool per model call
instead, which is the same move `McpTools` makes for MCP tools.

The transport is the A2A v1.0 JSON-RPC binding (`SendStreamingMessage`,
`SubscribeToTask`, `ListTasks`, `CancelTask`), so any A2A server works, not only an
Agent Server.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, unquote

import httpx
from langsmith.run_helpers import get_current_run_tree
from langsmith.run_trees import RunTree

from custom_demo.runtime.mcp_servers import slugify

logger = logging.getLogger(__name__)

CARD_TTL_SECONDS = 60
CARD_TIMEOUT_SECONDS = 15

# Header the Agent Server's A2A binding reads to return only this task's messages.
_HISTORY_SCOPE = {"LangGraph-A2A-History-Scope": "task"}

# Marks a message this module enqueued, so a prompt (and a reader of the thread) can
# tell a background result from something the user typed.
NOTIFICATION_PREFIX = "[background task finished]"


@dataclass(frozen=True)
class RemoteAgentConfig:
    """One remote agent entry as the assistant stores it."""

    id: str
    label: str
    url: str
    token: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    def request_headers(self) -> dict[str, str]:
        """Headers for every request to this agent: its token, then any extras."""
        base = {"x-api-key": self.token} if self.token else {}
        return {**base, **self.headers}


@dataclass(frozen=True)
class RemoteAgent:
    """A remote agent resolved from its card, ready to be offered as a subagent."""

    name: str
    label: str
    description: str
    endpoint: str
    headers: dict[str, str]


def parse_agents(raw: Any) -> tuple[RemoteAgentConfig, ...]:
    """Validate `context.remote_agents`, skipping entries with no URL.

    Accepts the same shape as `mcp_servers`: `[{id?, label, url, token?, headers?}]`.
    The id becomes the subagent name the model calls, so it is slugged to the
    characters a tool argument can carry (lowercase letters, digits, underscores).
    """
    if not isinstance(raw, list):
        return ()

    parsed: list[RemoteAgentConfig] = []
    for entry in raw:
        if not isinstance(entry, dict) or not str(entry.get("url") or "").strip():
            continue

        url = str(entry["url"]).strip()
        label = str(entry.get("label") or entry.get("id") or url).strip()
        headers = entry.get("headers") if isinstance(entry.get("headers"), dict) else {}
        parsed.append(
            RemoteAgentConfig(
                id=slugify(str(entry.get("id") or label)),
                label=label,
                url=url,
                token=str(entry["token"]).strip() if entry.get("token") else None,
                headers={str(k): str(v) for k, v in (headers or {}).items()},
            )
        )

    return tuple(parsed)


# --------------------------------------------------------------------------- #
# Agent cards                                                                   #
# --------------------------------------------------------------------------- #

# fingerprint -> (expires_at, agent or error text). An error is cached as briefly as a
# card, so one unreachable agent costs one timeout a minute, not one per model call.
_CARDS: dict[str, tuple[float, RemoteAgent | str]] = {}


def _fingerprint(config: RemoteAgentConfig) -> str:
    """Cache key that changes when anything a user can edit in Settings changes."""
    blob = json.dumps([config.id, config.url, config.token, config.headers], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def _card_url(url: str) -> str:
    """Where the agent card is, given either a card URL or an A2A endpoint.

    An Agent Server serves a card per assistant at
    `/.well-known/agent-card.json?assistant_id=<id>` and speaks A2A at
    `/a2a/<id>`, so an endpoint URL is mapped onto its card. Any other URL is taken to
    be a card URL already.
    """
    if "/a2a/" in url and "agent-card" not in url:
        base, _, assistant_id = url.rstrip("/").rpartition("/a2a/")
        return f"{base}/.well-known/agent-card.json?assistant_id={assistant_id}"

    return url


async def fetch_card(config: RemoteAgentConfig) -> RemoteAgent:
    """Read one agent card and resolve the agent's A2A endpoint from it."""
    headers = config.request_headers()
    async with httpx.AsyncClient(timeout=CARD_TIMEOUT_SECONDS, headers=headers) as http:
        response = await http.get(_card_url(config.url))

    response.raise_for_status()
    card = response.json()
    interfaces = card.get("supportedInterfaces") or []
    endpoint = next(
        (i["url"] for i in interfaces if str(i.get("protocolBinding", "")).lower() == "jsonrpc"),
        card.get("url"),
    )
    if not endpoint:
        raise ValueError("the agent card names no JSON-RPC endpoint")

    return RemoteAgent(
        name=config.id,
        label=config.label or card.get("name") or config.id,
        description=str(card.get("description") or card.get("name") or config.label),
        endpoint=str(endpoint),
        headers=headers,
    )


async def _resolve(config: RemoteAgentConfig) -> RemoteAgent | str:
    """One agent from cache or its card; an error string when it cannot be reached."""
    key = _fingerprint(config)
    cached = _CARDS.get(key)
    if cached and time.monotonic() < cached[0]:
        return cached[1]

    try:
        result: RemoteAgent | str = await fetch_card(config)
    except Exception as exc:  # noqa: BLE001 - reported to the model by name, never raised
        result = f"{type(exc).__name__}: {exc}"
        logger.warning("remote agent %s (%s) unreachable: %s", config.label, config.url, result)

    _CARDS[key] = (time.monotonic() + CARD_TTL_SECONDS, result)
    return result


async def load_agents(
    configs: tuple[RemoteAgentConfig, ...],
) -> tuple[list[RemoteAgent], dict[str, str]]:
    """Every configured agent that answered, and the error for each that did not.

    Concurrent and independent: one dead agent must not take its healthy neighbours'
    subagents away, which is the rule MCP discovery follows too.
    """
    results = await asyncio.gather(*(_resolve(c) for c in configs))
    agents = [r for r in results if isinstance(r, RemoteAgent)]
    down = {c.label: r for c, r in zip(configs, results, strict=True) if isinstance(r, str)}
    return agents, down


# --------------------------------------------------------------------------- #
# A2A transport                                                                 #
# --------------------------------------------------------------------------- #


# Marks trace headers as sent by a remote-subagent call, which is the only inbound parent
# `graph.py` accepts; voice mode sends `langsmith-trace` too and keeps its own roots.
TRACE_MARKER = "remote_subagent_parent"


def trace_headers() -> dict[str, str]:
    """Headers that make the remote agent's run a child of the current span.

    Called inside the `task` or `start_remote_task` tool, so the remote run nests under
    that tool call in the orchestrator's trace. The baggage names the orchestrator's
    project, because a trace renders as one tree only within one project. Empty when
    nothing is being traced.
    """
    run = get_current_run_tree()
    if run is None:
        return {}

    metadata = {**(run.metadata or {}), TRACE_MARKER: True}
    baggage = f"langsmith-metadata={quote(json.dumps(metadata))},langsmith-project={quote(run.session_name)}"
    return {"langsmith-trace": run.to_headers()["langsmith-trace"], "baggage": baggage}


def remote_parent(configurable: dict) -> RunTree | None:
    """The span to nest this run under, when another agent called it as a remote subagent.

    Agent Server copies inbound `langsmith-trace` and baggage headers into `configurable`.
    Only a parent carrying `TRACE_MARKER` in its metadata is accepted: those come from
    `trace_headers` above, over A2A. Measured on Agent Server 0.13.0,
    a run started that way nests under the caller's `task` span with its full tree.

    Voice mode sends `langsmith-trace` too, for its browser-side tool span, and on 0.11.1
    and 0.13.0 a run given THAT parent vanished from LangSmith with no error (ingestion is
    asynchronous). So voice runs keep their own roots, and the voice shell records the
    run id on its span instead (`closeToolSpan`). Do not widen the marker check to accept
    them without re-measuring that path.
    """
    header = configurable.get("langsmith-trace")
    metadata = configurable.get("langsmith-metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(unquote(metadata))
        except ValueError:
            return None

    if not header or not isinstance(metadata, dict) or not metadata.get(TRACE_MARKER):
        return None

    project = configurable.get("langsmith-project")
    # Decoded first: whether the server decoded the baggage value is not something to rely on.
    baggage = f"langsmith-project={quote(unquote(str(project)))}" if project else ""
    return RunTree.from_headers({"langsmith-trace": str(header), "baggage": baggage})


class RemoteAgentError(RuntimeError):
    """A remote agent refused or failed a request; the message is for the model."""


def _rpc_body(method: str, params: dict[str, Any]) -> dict[str, Any]:
    """A JSON-RPC request envelope with a fresh id."""
    return {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method, "params": params}


def _raise_for_error(agent: RemoteAgent, payload: dict[str, Any]) -> None:
    """Raise `RemoteAgentError` when a JSON-RPC response carries an error."""
    if "error" in payload:
        error = payload["error"]
        raise RemoteAgentError(f"{agent.label}: {error.get('message', error)}")


async def _rpc(
    agent: RemoteAgent, method: str, params: dict[str, Any], timeout: float | None
) -> Any:
    """One JSON-RPC call to an agent's A2A endpoint, returning `result`."""
    headers = {**agent.headers, **_HISTORY_SCOPE}
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as http:
        response = await http.post(agent.endpoint, json=_rpc_body(method, params))

    response.raise_for_status()
    payload = response.json()
    _raise_for_error(agent, payload)
    return payload["result"]


async def _sse_results(
    agent: RemoteAgent, method: str, params: dict[str, Any], trace: dict[str, str] | None = None
) -> AsyncIterator[dict[str, Any]]:
    """Each JSON-RPC `result` a streaming A2A method sends, as it arrives."""
    headers = {**agent.headers, **(trace or {}), "Accept": "text/event-stream"}
    async with (
        httpx.AsyncClient(timeout=httpx.Timeout(30, read=None), headers=headers) as http,
        http.stream("POST", agent.endpoint, json=_rpc_body(method, params)) as response,
    ):
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue

            payload = json.loads(line[5:])
            _raise_for_error(agent, payload)
            yield payload.get("result") or {}


def stream_message(
    agent: RemoteAgent, text: str, context_id: str, trace: dict[str, str] | None = None
) -> AsyncIterator[dict[str, Any]]:
    """`SendStreamingMessage`: the task's events as it works, ending with its final status."""
    return _sse_results(
        agent,
        "SendStreamingMessage",
        {
            "message": {
                "role": "ROLE_USER",
                "parts": [{"text": text}],
                "messageId": str(uuid.uuid4()),
                "contextId": context_id,
            }
        },
        trace,
    )


def subscribe(agent: RemoteAgent, task_id: str) -> AsyncIterator[dict[str, Any]]:
    """`SubscribeToTask`: reattach to a running task (`custom_demo/web/a2a.py` adds it)."""
    return _sse_results(agent, "SubscribeToTask", {"id": task_id})


def _texts(parts: list[dict[str, Any]]) -> list[str]:
    """The text of every A2A part that carries some."""
    return [p["text"] for p in parts if "text" in p]


def progress(event: dict[str, Any]) -> list[dict[str, Any]]:
    """An A2A stream event in the compact form the UI draws: steps, text, answer, state.

    `step` is one tool result the remote agent produced, cut to its first line; `text`
    is the answer so far (each frame carries all of it, not a delta); `state` is a
    status change, `final` on the last one.
    """
    if "task" in event:
        task = event["task"]
        return [{"kind": "task", "task_id": task.get("id"), "state": task_state(task)}]

    if event.get("kind") == "artifact-update":
        parts = (event.get("artifact") or {}).get("parts", [])
        return [{"kind": "answer", "text": "\n\n".join(_texts(parts))}]

    if event.get("kind") != "status-update":
        return []

    status = event.get("status") or {}
    out: list[dict[str, Any]] = []
    for part in (status.get("message") or {}).get("parts", []):
        for result in (part.get("data") or {}).get("tool_results", []):
            first = str(result.get("content", "")).strip().splitlines() or [""]
            out.append({"kind": "step", "text": first[0][:160]})

        if part.get("text"):
            out.append({"kind": "text", "text": part["text"]})

    if event.get("final") or not out:
        out.append({"kind": "state", "state": task_state(event), "final": bool(event.get("final"))})

    return out


async def run_streaming(
    agent: RemoteAgent,
    text: str,
    context_id: str,
    on_progress: Callable[[dict[str, Any]], None],
    trace: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Send a message, report its progress as it streams, and return the finished task.

    The returned task has the shape `task_text` and `task_state` read, built from what
    streamed: the answer artifact (or the last text), and the final state.
    """
    answer = ""
    latest_text = ""
    state = "TASK_STATE_WORKING"
    async for event in stream_message(agent, text, context_id, trace):
        for item in progress(event):
            on_progress(item)
            if item["kind"] == "answer":
                answer = item["text"]
            elif item["kind"] == "text":
                latest_text = item["text"]
            elif item["kind"] == "state":
                state = f"TASK_STATE_{item['state'].upper()}"

    return {
        "status": {"state": state},
        "artifacts": [{"parts": [{"text": answer or latest_text}]}],
    }


async def latest_task(agent: RemoteAgent, context_id: str) -> dict[str, Any] | None:
    """The newest task in a context, asked of the agent itself."""
    result = await _rpc(agent, "ListTasks", {"contextId": context_id}, timeout=30)
    tasks = result.get("tasks") or []
    return tasks[0] if tasks else None


async def cancel_task(agent: RemoteAgent, task_id: str) -> None:
    """Ask the agent to stop a task."""
    await _rpc(agent, "CancelTask", {"id": task_id}, timeout=30)


def task_state(task: dict[str, Any]) -> str:
    """A task's state in plain words: working, completed, failed, canceled."""
    raw = str((task.get("status") or {}).get("state", "unknown"))
    return raw.removeprefix("TASK_STATE_").lower()


def task_text(task: dict[str, Any]) -> str:
    """The agent's answer: its artifacts' text, else its status message's, else its history's."""
    texts = [t for a in task.get("artifacts") or [] for t in _texts(a.get("parts", []))]
    if texts:
        return "\n\n".join(texts)

    message = (task.get("status") or {}).get("message") or {}
    texts = _texts(message.get("parts", []))
    if texts:
        return "\n\n".join(texts)

    for entry in reversed(task.get("history") or []):
        if entry.get("role") in ("ROLE_AGENT", "agent"):
            return "\n\n".join(_texts(entry.get("parts", [])))

    return ""


# --------------------------------------------------------------------------- #
# Background tasks and wake-up                                                  #
# --------------------------------------------------------------------------- #


@dataclass
class BackgroundTask:
    """One remote task running on behalf of a thread, held by this process."""

    task_id: str
    agent: RemoteAgent
    description: str
    parent_thread: str | None
    parent_assistant: str | None
    started: float = field(default_factory=time.time)
    job: asyncio.Task | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    cancelled: bool = False
    # Progress so far, replayed to anyone who starts watching late, and the queues of
    # everyone watching now (`web/remote_agents.py:remote_task_stream`).
    events: list[dict[str, Any]] = field(default_factory=list)
    watchers: set[asyncio.Queue] = field(default_factory=set)
    # The remote agent's own id for the task, once its first event names it.
    remote_task_id: str | None = None
    # Captured when the task starts, inside `start_remote_task`, so the remote run nests
    # under that tool call even though it finishes long after the call returned.
    trace: dict[str, str] = field(default_factory=dict)

    def publish(self, item: dict[str, Any]) -> None:
        """Record one progress item and hand it to every current watcher."""
        if item["kind"] == "task" and item.get("task_id"):
            self.remote_task_id = str(item["task_id"])

        self.events.append(item)
        for queue in self.watchers:
            queue.put_nowait(item)

    @property
    def done(self) -> bool:
        """Whether nothing more will be published."""
        return self.job is not None and self.job.done()


# task id -> task. Process-wide, like the MCP and card caches. A restart loses the
# in-flight jobs; `check_remote_task` then asks the agent (`ListTasks`) instead.
_TASKS: dict[str, BackgroundTask] = {}


def get_task(task_id: str) -> BackgroundTask | None:
    """A background task this process started."""
    return _TASKS.get(task_id.strip())


def start_background(
    agent: RemoteAgent, description: str, parent_thread: str | None, parent_assistant: str | None
) -> BackgroundTask:
    """Start a remote task without waiting for it; its completion wakes the parent thread."""
    task = BackgroundTask(
        task_id=str(uuid.uuid4()),
        agent=agent,
        description=description,
        parent_thread=parent_thread,
        parent_assistant=parent_assistant,
        trace=trace_headers(),
    )
    _TASKS[task.task_id] = task
    task.job = asyncio.get_running_loop().create_task(_run(task))
    return task


def restart_background(task: BackgroundTask, description: str) -> None:
    """Run an existing task again with new instructions, in the same remote context."""
    task.cancelled = False
    task.result = None
    task.error = None
    task.description = description
    task.job = asyncio.get_running_loop().create_task(_run(task))


async def _run(task: BackgroundTask) -> None:
    """Drive one remote task to its end, then report back to the thread that started it."""
    try:
        task.result = await run_streaming(
            task.agent, task.description, task.task_id, task.publish, task.trace
        )
    except asyncio.CancelledError:
        task.cancelled = True
        task.publish({"kind": "state", "state": "canceled", "final": True})
        return
    except Exception as exc:  # noqa: BLE001 - the failure is the notification's content
        task.error = f"{type(exc).__name__}: {exc}"
        task.publish({"kind": "state", "state": "failed", "final": True, "error": task.error})

    await _notify(task)


def notification_text(task: BackgroundTask) -> str:
    """The message a finished task leaves on its parent thread."""
    head = f"{NOTIFICATION_PREFIX} {task.agent.name} task_id={task.task_id}"
    if task.error:
        return f"{head}\nstatus: failed\nerror: {task.error}"

    assert task.result is not None
    state = task_state(task.result)
    return f"{head}\nstatus: {state}\n\n{task_text(task.result) or '(no text in the answer)'}"


async def _notify(task: BackgroundTask) -> None:
    """Enqueue a run on the parent thread carrying the result.

    `multitask_strategy="enqueue"` is the whole wake-up mechanism: an idle thread runs
    it at once, and a thread in the middle of a run gets it as the next run, after the
    current one ends. The client is the in-process one (no URL), so this reaches the
    Agent Server hosting the parent without leaving the process.
    """
    if not (task.parent_thread and task.parent_assistant):
        logger.warning("remote task %s finished with no parent thread to notify", task.task_id)
        return

    # Deferred: the SDK's in-process client is only meaningful inside the Agent Server,
    # and importing it at module load would bind every unit test to it.
    from langgraph_sdk import get_client  # noqa: PLC0415 - see above

    client = get_client(api_key=os.getenv("APP_SHARED_SECRET", "").strip() or None)
    try:
        await client.runs.create(
            task.parent_thread,
            task.parent_assistant,
            input={"messages": [{"role": "user", "content": notification_text(task)}]},
            metadata={"remote_task_id": task.task_id, "remote_agent": task.agent.name},
            multitask_strategy="enqueue",
            # What the SPA streams with, and resumable, so a browser that notices this
            # run late can join it and replay it from the first event.
            stream_mode=["messages", "updates", "custom"],
            stream_subgraphs=True,
            stream_resumable=True,
        )
    except Exception:
        logger.exception(
            "could not notify thread %s of remote task %s", task.parent_thread, task.task_id
        )
