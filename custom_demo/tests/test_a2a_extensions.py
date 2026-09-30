"""The A2A methods `web/a2a.py` adds, and the private built-ins it leans on.

`web/a2a.py` reuses `langgraph_api`'s own A2A helpers so its events match the built-in
stream exactly. Those helpers are private, so the first test fails loudly when an
upgrade moves one, instead of the shim breaking at the first subscribe on stage.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from custom_demo.runtime import remote_agents as ra
from custom_demo.web import a2a

USED = (
    "handle_a2a_assistant_endpoint",
    "handle_agent_card_endpoint",
    "handle_tasks_get",
    "_parse_task_id",
    "_client",
    "_lc_items_to_status_update_event",
    "_create_response_artifact_update",
    "_extract_a2a_response",
    "_serialize_a2a_sse_payload",
    "_lg_status_to_a2a_state",
    "ERROR_CODE_INVALID_PARAMS",
    "ERROR_CODE_TASK_NOT_FOUND",
)


def _builtin_source() -> str:
    """The built-in A2A module's source, read rather than imported.

    Importing it needs a running Agent Server's configuration, which is exactly why
    `web/a2a.py` defers the import; a test that imported it would need the same.
    """
    # From the top-level package: resolving the submodule would import `langgraph_api.api`.
    spec = importlib.util.find_spec("langgraph_api")
    assert spec is not None and spec.origin
    return (Path(spec.origin).parent / "api" / "a2a.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", USED)
def test_the_builtins_the_shim_uses_still_exist(name):
    source = _builtin_source()
    defined = f"def {name}(" in source or f"\n{name} =" in source
    assert defined, f"langgraph_api.api.a2a.{name} moved; update web/a2a.py"


@pytest.mark.parametrize(
    "params",
    [
        {"taskId": "t1", "pushNotificationConfig": {"url": "https://hook.test/a", "token": "k"}},
        {"taskId": "t1", "config": {"url": "https://hook.test/a", "token": "k"}},
        {"parent": "tasks/t1", "url": "https://hook.test/a", "token": "k"},
    ],
)
def test_push_configs_are_read_from_v03_and_v10_layouts(params):
    task_id, config = a2a._config_params(params)
    assert task_id == "t1"
    assert config["url"] == "https://hook.test/a"
    assert config["token"] == "k"


def test_every_method_the_shim_answers_is_one_the_builtin_does_not():
    ours = a2a._SUBSCRIBE | set(a2a._PUSH_METHODS)
    builtin_methods = {
        "SendMessage",
        "SendStreamingMessage",
        "GetTask",
        "CancelTask",
        "ListTasks",
        "GetExtendedAgentCard",
    }
    assert not ours & builtin_methods


def test_progress_turns_stream_events_into_steps_text_and_state():
    tool = {"tool_results": [{"content": "  first line\nsecond"}]}
    working = {
        "kind": "status-update",
        "status": {
            "state": "TASK_STATE_WORKING",
            "message": {"parts": [{"data": tool}, {"text": "So far"}]},
        },
    }
    final = {"kind": "status-update", "status": {"state": "TASK_STATE_COMPLETED"}, "final": True}
    assert ra.progress(working) == [
        {"kind": "step", "text": "first line"},
        {"kind": "text", "text": "So far"},
    ]
    assert ra.progress(final) == [{"kind": "state", "state": "completed", "final": True}]
