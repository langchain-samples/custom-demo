"""Test a remote A2A agent before a conversation depends on it.

The SPA cannot read an agent card itself: the card may sit behind a token the
browser should not hold, and on another origin. This route reads it the way the
runtime will (`runtime/remote_agents.py`) and reports what the model will be told:
the agent's name, what it says it does, and the endpoint calls will go to.
"""

from __future__ import annotations

import asyncio
import json

from starlette.responses import JSONResponse, StreamingResponse

from custom_demo.runtime import remote_agents as ra
from custom_demo.web.a2a import server_client
from custom_demo.web.errors import err


async def remote_agent_probe(request):
    """POST {label, url, token?} -> {ok, name, description, endpoint} or {ok: false, error}."""
    body = await request.json()
    configs = ra.parse_agents([body] if isinstance(body, dict) else [])
    if not configs:
        return err(
            400, "missing_url", "A remote agent needs a URL: its agent card or A2A endpoint."
        )

    config = configs[0]
    try:
        agent = await ra.fetch_card(config)
    except Exception as exc:  # noqa: BLE001 - the failure is the answer this route exists to give
        return JSONResponse({"ok": False, "id": config.id, "error": f"{type(exc).__name__}: {exc}"})

    return JSONResponse(
        {
            "ok": True,
            "id": config.id,
            "name": agent.label,
            "description": agent.description,
            "endpoint": agent.endpoint,
        }
    )


async def remote_task_stream(request):
    """GET a background remote task's progress as SSE: what happened so far, then live.

    A task this process is running is replayed from its buffer and then followed. One
    it is not (the server restarted, or another worker holds it) is reattached with the
    remote agent's own `SubscribeToTask`, located from the thread that started it:
    `?thread_id=` names the thread, whose state records which agent the task went to,
    and the agent is resolved from that thread's assistant configuration. A URL is never
    taken from the caller.
    """
    task_id = request.path_params["task_id"]
    held = ra.get_task(task_id)
    if held is not None:
        return StreamingResponse(_follow_held(held), media_type="text/event-stream")

    thread_id = request.query_params.get("thread_id", "")
    agent, remote_id = await _locate(thread_id, task_id)
    if agent is None or remote_id is None:
        return err(
            404, "unknown_task", f"No remote task {task_id} on thread {thread_id or '(none)'}."
        )

    return StreamingResponse(_follow_remote(agent, remote_id), media_type="text/event-stream")


def _frame(item: dict) -> bytes:
    """One progress item as an SSE frame."""
    return b"data: " + json.dumps(item).encode() + b"\n\n"


async def _follow_held(task: ra.BackgroundTask):
    """Replay a held task's progress, then stream what it publishes until it ends."""
    queue: asyncio.Queue = asyncio.Queue()
    task.watchers.add(queue)
    try:
        for item in list(task.events):
            yield _frame(item)

        while not task.done or not queue.empty():
            try:
                item = await asyncio.wait_for(queue.get(), timeout=15)
            except TimeoutError:
                yield b": keep-alive\n\n"
                continue

            yield _frame(item)
            if item.get("final"):
                return
    finally:
        task.watchers.discard(queue)


async def _follow_remote(agent: ra.RemoteAgent, remote_id: str):
    """Reattach to a task on the remote agent and stream its progress."""
    try:
        async for event in ra.subscribe(agent, remote_id):
            for item in ra.progress(event):
                yield _frame(item)
    except Exception as exc:  # noqa: BLE001 - reported to the watcher as the stream's last frame
        yield _frame(
            {
                "kind": "state",
                "state": "unknown",
                "final": True,
                "error": f"{type(exc).__name__}: {exc}",
            }
        )


async def _locate(thread_id: str, task_id: str) -> tuple[ra.RemoteAgent | None, str | None]:
    """The remote agent a thread's task went to, and that agent's own id for the task."""
    if not thread_id:
        return None, None

    client = server_client()
    state = await client.threads.get_state(thread_id)
    values = state.get("values")
    tasks = values.get("remote_tasks") if isinstance(values, dict) else None
    record = tasks.get(task_id) if isinstance(tasks, dict) else None
    thread = await client.threads.get(thread_id)
    assistant_id = (thread.get("metadata") or {}).get("assistant_id")
    if not record or not assistant_id:
        return None, None

    assistant = await client.assistants.get(str(assistant_id))
    context = assistant.get("context")
    configs = ra.parse_agents(context.get("remote_agents") if isinstance(context, dict) else None)
    config = next((c for c in configs if c.id == record["agent"]), None)
    if config is None:
        return None, None

    agent = await ra.fetch_card(config)
    latest = await ra.latest_task(agent, task_id)
    return agent, str(latest["id"]) if latest else None
