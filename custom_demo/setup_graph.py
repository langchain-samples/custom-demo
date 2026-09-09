"""Agent Server adapter for demo preparation, registered as `assistant_setup`.

The setup node delegates discovery, planning and resource provisioning to
`prepare_assistant`, returning metadata, context and prompt URLs under `result`.
The browser publishes that payload as an assistant in a separate operation.
"""

from __future__ import annotations

import contextlib
import os
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context

from custom_demo.provisioning.setup import prepare_assistant

_INPUT_KEYS = (
    "workspace",
    "customer",
    "owner",
    "industry",
    "website",
    "use_case",
    "failure_mode",
    "hallucination",
    "push_prompts",
    "enabled_tools",
    "demo_traffic",
)


class SetupState(TypedDict, total=False):
    """Setup-run state: the setup inputs plus the `result`/`status`/`error` output."""

    workspace: str
    customer: str
    owner: str
    industry: str
    website: str
    use_case: str
    failure_mode: str
    hallucination: bool
    push_prompts: bool
    enabled_tools: list[str]
    # Backfill the trace project with synthetic traffic. Opt-in; see prepare_assistant.
    demo_traffic: bool
    result: dict
    status: str
    error: str


def _run(state: SetupState) -> dict:
    payload = {k: state.get(k) for k in _INPUT_KEYS if state.get(k) is not None}
    if not payload.get("workspace") or not payload.get("customer"):
        return {"status": "error", "error": "workspace and customer are required"}

    try:
        return {"result": prepare_assistant(payload), "status": "ok"}
    except Exception as exc:  # noqa: BLE001 - surface to the SPA rather than 500
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


# ty doesn't recognize our TypedDict as satisfying langgraph's StateLike bound
# (a third-party typing gap); the TypedDict is valid langgraph state at runtime.
_builder = StateGraph(SetupState)  # ty: ignore[invalid-argument-type]
_builder.add_node("setup", _run)
_builder.add_edge(START, "setup")
_builder.add_edge("setup", END)
_compiled = _builder.compile()

# Route the whole setup run — including the analyze_customer LLM call — to a
# dedicated project in the deployment's workspace (Josiah Coad), so demo-setup
# activity is observable on its own rather than in the server default project.
# Override the project via SETUP_TRACE_PROJECT.
SETUP_TRACE_PROJECT = os.getenv("SETUP_TRACE_PROJECT", "custom-demos")


@contextlib.asynccontextmanager
async def graph(config: Any):
    """Factory: wrap each setup run in a tracing context, then yield the graph.

    Traces land in SETUP_TRACE_PROJECT (Agent Server pattern — mirrors
    custom_demo/graph.py).
    """
    with tracing_context(enabled=True, project_name=SETUP_TRACE_PROJECT):
        yield _compiled
