"""Deployed assistant-setup graph (Part 3).

A second graph on the same Agent Server. Given setup inputs (workspace, customer,
owner, industry, website, use_case, failure_mode, push_prompts), it fetches brand
assets, generates persona quick-actions + an LLM tool selection, optionally pushes
prompts to the workspace's Prompt Hub, and returns a ready assistant payload
(`result`: metadata + context + prompt_urls). The SPA creates the assistant from it.

Registered in langgraph.json as `assistant_setup`.
"""

from __future__ import annotations

import contextlib
import os
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context

from dashboard_agent.assistant_setup import prepare_assistant

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
    "prompt_source",
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
    prompt_source: Literal["prompt_hub", "context_hub"]
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
    except Exception as exc:  # surface to the SPA rather than 500
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
    dashboard_agent/graph.py).
    """
    with tracing_context(enabled=True, project_name=SETUP_TRACE_PROJECT):
        yield _compiled
