"""A2A methods the Agent Server does not implement yet: task subscription and push notifications.

The Agent Server's A2A binding answers `SubscribeToTask` and the
`*TaskPushNotificationConfig` methods with -32601. Custom routes take priority over
the built-in ones, so this module sits in front of `/a2a/{assistant_id}`: it answers
those methods itself and hands every other request to the built-in handler unchanged.

  * `SubscribeToTask` reattaches to a task that is still running, which is what a
    client needs after a page reload or a restart. The first event is the task as it
    stands (the built-in `GetTask`); if it is still working, the stream then follows
    the task's run live and emits the same `status-update` and `artifact-update`
    events `SendStreamingMessage` does, built with the same helpers.
  * Push notifications let a client that is not listening be told when a task ends:
    a registered config gets the final task POSTed to its URL, with the config's
    token in `X-A2A-Notification-Token`, as the spec describes.

Push configs live in this process. A restart drops them, so a client that must not
miss a completion should re-register on reconnect.

The built-in helpers are private to `langgraph_api` and pinned by the version this
repo locks; `custom_demo/tests/test_a2a_extensions.py` fails if they move.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
from starlette.responses import JSONResponse, Response, StreamingResponse

logger = logging.getLogger(__name__)


def builtin_a2a() -> Any:
    """The Agent Server's own A2A module, imported on first use.

    Importing it reads the server's runtime configuration (`REDIS_URI` and friends), so
    a module-level import would make this file, and `webapp.py` with it, unimportable
    anywhere but inside a running Agent Server: CI's import smoke and the unit tests.
    """
    from langgraph_api.api import a2a  # noqa: PLC0415 - needs the server's config, see above

    return a2a


def server_client() -> Any:
    """The Agent Server's in-process SDK client, the one its A2A handlers use."""
    return builtin_a2a()._client()


TERMINAL_STATES = frozenset(
    {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED", "TASK_STATE_REJECTED"}
)
# States a task can sit in without its run still going: nothing more will stream.
_SETTLED_STATES = TERMINAL_STATES | {"TASK_STATE_INPUT_REQUIRED", "TASK_STATE_AUTH_REQUIRED"}

_SUBSCRIBE = {"SubscribeToTask", "tasks/resubscribe"}
_PUSH_METHODS = {
    "CreateTaskPushNotificationConfig": "set",
    "SetTaskPushNotificationConfig": "set",
    "tasks/pushNotificationConfig/set": "set",
    "GetTaskPushNotificationConfig": "get",
    "tasks/pushNotificationConfig/get": "get",
    "ListTaskPushNotificationConfig": "list",
    "ListTaskPushNotificationConfigs": "list",
    "tasks/pushNotificationConfig/list": "list",
    "DeleteTaskPushNotificationConfig": "delete",
    "tasks/pushNotificationConfig/delete": "delete",
}

PUSH_EXTENSION_URI = "https://langchain.com/a2a/extensions/custom-demo-subscribe-push/v1"


def _rpc_error(rpc_id: Any, code: int, message: str) -> JSONResponse:
    """A JSON-RPC error response."""
    return JSONResponse(
        {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}
    )


def _rpc_result(rpc_id: Any, result: Any) -> JSONResponse:
    """A JSON-RPC success response."""
    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": result})


async def a2a_endpoint(request):
    """`/a2a/{assistant_id}`: the extra methods here, everything else to the built-in handler."""
    if request.method != "POST":
        return await builtin_a2a().handle_a2a_assistant_endpoint(request)

    body = await request.body()
    try:
        message = json.loads(body)
    except ValueError:
        # Malformed JSON is the built-in handler's to answer, in its own words.
        return await builtin_a2a().handle_a2a_assistant_endpoint(request)

    method = message.get("method") if isinstance(message, dict) else None
    if method in _SUBSCRIBE:
        return await _subscribe(request, message)

    if method in _PUSH_METHODS:
        return await _push_config(request, message, _PUSH_METHODS[method])

    return await builtin_a2a().handle_a2a_assistant_endpoint(request)


async def agent_card(request):
    """The domain-root agent card, advertising the capabilities this module adds."""
    response = await builtin_a2a().handle_agent_card_endpoint(request)
    if response.status_code != 200:
        return response

    card = json.loads(bytes(response.body))
    capabilities = card.setdefault("capabilities", {})
    capabilities["pushNotifications"] = True
    capabilities.setdefault("extensions", []).append(
        {
            "uri": PUSH_EXTENSION_URI,
            "description": "SubscribeToTask and task push notification configs",
            "required": False,
        }
    )
    return JSONResponse(card)


# --------------------------------------------------------------------------- #
# SubscribeToTask                                                               #
# --------------------------------------------------------------------------- #


async def _subscribe(request, message: dict) -> Response:
    """Stream a task: its current state, then its live events until it ends."""
    rpc_id = message.get("id")
    params = message.get("params") or {}
    task_id = str(params.get("id") or "")
    try:
        context_id, run_id = builtin_a2a()._parse_task_id(task_id)
    except ValueError as exc:
        return _rpc_error(rpc_id, builtin_a2a().ERROR_CODE_INVALID_PARAMS, str(exc))

    current = await builtin_a2a().handle_tasks_get(request, {"id": task_id, "historyScope": "task"})
    if "error" in current:
        return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "error": current["error"]})

    task = current["result"]

    async def events() -> AsyncIterator[bytes]:
        yield _sse(rpc_id, {"task": task})
        state = (task.get("status") or {}).get("state")
        if state in _SETTLED_STATES:
            return

        async for event in _follow_run(request, task_id, context_id, run_id):
            yield _sse(rpc_id, event)

    return StreamingResponse(events(), media_type="text/event-stream")


def _sse(rpc_id: Any, result: Any) -> bytes:
    """One JSON-RPC result as an SSE `data:` frame, encoded the way the built-in encodes it."""
    payload = builtin_a2a()._serialize_a2a_sse_payload(
        {"jsonrpc": "2.0", "id": rpc_id, "result": result}
    )
    return b"data: " + payload + b"\n\n"


async def _follow_run(request, task_id: str, context_id: str, run_id: str) -> AsyncIterator[dict]:
    """The task's A2A events from its run's live stream, ending with its final status.

    Mirrors the event loop in `builtin_a2a().handle_message_stream`: `messages/*` chunks become
    working status updates, the last `values` chunk becomes the answer artifact.
    """
    client = server_client()
    result: Any = None
    failed = False
    # No `stream_mode`: the run already streams what it was created with, and naming
    # "messages" here filters out the `messages/partial` frames it actually emits.
    stream = client.runs.join_stream(context_id, run_id, headers=request.headers)
    async for chunk in stream:
        if chunk.event == "values":
            result = chunk.data
        elif chunk.event == "error":
            failed = True
        elif chunk.event.startswith("messages") and isinstance(chunk.data, list) and chunk.data:
            update = builtin_a2a()._lc_items_to_status_update_event(
                chunk.data, task_id=task_id, context_id=context_id, state="TASK_STATE_WORKING"
            )
            parts = (update.get("status") or {}).get("message", {}).get("parts", [])
            if any(p.get("text") or p.get("data") for p in parts):
                yield update

    # The run has ended. Its values stream may have been skipped (a join can start after
    # the last values chunk), so the task's own record is what decides the ending.
    final = await builtin_a2a().handle_tasks_get(request, {"id": task_id, "historyScope": "task"})
    task = final.get("result") or {}
    state = (task.get("status") or {}).get("state") or (
        "TASK_STATE_FAILED" if failed else "TASK_STATE_COMPLETED"
    )
    if state == "TASK_STATE_COMPLETED":
        parts = _answer_parts(result, task)
        if parts:
            yield builtin_a2a()._create_response_artifact_update(
                task_id=task_id,
                context_id=context_id,
                parts=parts,
                assistant_id=request.path_params.get("assistant_id", ""),
            )

    yield {
        "taskId": task_id,
        "contextId": context_id,
        "kind": "status-update",
        "status": {"state": state, "timestamp": datetime.now(UTC).isoformat()},
        "final": True,
    }


def _answer_parts(result: Any, task: dict) -> list[dict]:
    """The answer's parts, from the run's final values or else the task's own artifacts."""
    if isinstance(result, dict):
        try:
            return builtin_a2a()._extract_a2a_response(result)
        except Exception:  # fall back to the task record below
            logger.warning("could not extract an A2A answer from run values", exc_info=True)

    return [p for a in task.get("artifacts") or [] for p in a.get("parts", [])]


# --------------------------------------------------------------------------- #
# Push notifications                                                            #
# --------------------------------------------------------------------------- #

# task id -> config id -> config, as the client registered it.
_CONFIGS: dict[str, dict[str, dict]] = {}
# task id -> the watcher that sends its notifications.
_WATCHERS: dict[str, asyncio.Task] = {}


def _config_params(params: dict) -> tuple[str, dict]:
    """Task id and config from a set request, accepting v0.3 and v1.0 layouts."""
    nested = params.get("pushNotificationConfig") or params.get("config") or {}
    config = (
        {**nested}
        if nested
        else {k: v for k, v in params.items() if k not in ("taskId", "id", "parent")}
    )
    task_id = str(params.get("taskId") or params.get("id") or "")
    if not task_id and isinstance(params.get("parent"), str):
        task_id = params["parent"].removeprefix("tasks/")

    return task_id, config


async def _push_config(request, message: dict, action: str) -> Response:
    """Set, get, list or delete a task's push notification configs."""
    rpc_id = message.get("id")
    params = message.get("params") or {}
    if action == "set":
        return _set_push_config(request, rpc_id, params)

    task_id = str(params.get("taskId") or params.get("id") or "")
    configs = _CONFIGS.get(task_id, {})
    if action == "list":
        return _rpc_result(
            rpc_id, [{"taskId": task_id, "pushNotificationConfig": c} for c in configs.values()]
        )

    config_id = str(params.get("pushNotificationConfigId") or params.get("configId") or "")
    if action == "get":
        config = configs.get(config_id) or next(iter(configs.values()), None)
        if config is None:
            return _rpc_error(
                rpc_id, builtin_a2a().ERROR_CODE_TASK_NOT_FOUND, f"No push config for {task_id}."
            )

        return _rpc_result(rpc_id, {"taskId": task_id, "pushNotificationConfig": config})

    configs.pop(config_id, None)
    return _rpc_result(rpc_id, None)


def _set_push_config(request, rpc_id: Any, params: dict) -> Response:
    """Register a push config and start the task's watcher if none is running."""
    task_id, config = _config_params(params)
    if not task_id or not str(config.get("url") or "").startswith(("http://", "https://")):
        return _rpc_error(
            rpc_id,
            builtin_a2a().ERROR_CODE_INVALID_PARAMS,
            "A config needs a taskId and an http(s) url.",
        )

    try:
        context_id, run_id = builtin_a2a()._parse_task_id(task_id)
    except ValueError as exc:
        return _rpc_error(rpc_id, builtin_a2a().ERROR_CODE_INVALID_PARAMS, str(exc))

    config = {**config, "id": str(config.get("id") or uuid.uuid4())}
    _CONFIGS.setdefault(task_id, {})[config["id"]] = config
    if task_id not in _WATCHERS or _WATCHERS[task_id].done():
        headers = {
            k: v for k, v in request.headers.items() if k.lower() in ("x-api-key", "authorization")
        }
        _WATCHERS[task_id] = asyncio.get_running_loop().create_task(
            _watch(task_id, context_id, run_id, headers)
        )

    return _rpc_result(rpc_id, {"taskId": task_id, "pushNotificationConfig": config})


async def _watch(task_id: str, context_id: str, run_id: str, headers: dict[str, str]) -> None:
    """Wait for a task's run to end, then POST the final task to every config registered for it."""
    client = server_client()
    try:
        await client.runs.join(context_id, run_id, headers=headers)
    except Exception:  # a run that errors still ends; notify with its record
        logger.warning("join failed while watching task %s", task_id, exc_info=True)

    thread = await client.threads.get(context_id, headers=headers)
    runs = await client.runs.list(context_id, headers=headers)
    run = next((r for r in runs if r["run_id"] == run_id), None)
    state = builtin_a2a()._lg_status_to_a2a_state(run["status"]) if run else "TASK_STATE_UNKNOWN"
    task = {
        "kind": "task",
        "id": task_id,
        "contextId": context_id,
        "status": {"state": state, "timestamp": datetime.now(UTC).isoformat()},
        "artifacts": [
            {
                "artifactId": str(uuid.uuid4()),
                "name": "Assistant Response",
                "parts": _answer_parts(thread.get("values"), {}),
            }
        ],
    }
    async with httpx.AsyncClient(timeout=30) as http:
        for config in list(_CONFIGS.get(task_id, {}).values()):
            token = config.get("token")
            try:
                await http.post(
                    config["url"],
                    json=task,
                    headers={"X-A2A-Notification-Token": token} if token else {},
                )
            except Exception:  # one unreachable webhook must not stop the others
                logger.warning("push notification to %s failed", config["url"], exc_info=True)
