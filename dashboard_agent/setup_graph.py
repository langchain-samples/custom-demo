"""Deployed assistant-setup graph (Part 3).

A second graph on the same Agent Server. Given setup inputs (workspace, customer,
owner, industry, website, hallucination, push_prompts), it fetches brand assets,
generates persona quick-actions, optionally pushes prompts to the workspace's
Prompt Hub, and returns a ready assistant payload (`result`: metadata + context +
prompt_urls). The SPA creates the assistant from that payload.

Registered in langgraph.json as `assistant_setup`.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from dashboard_agent.assistant_setup import prepare_assistant

_INPUT_KEYS = (
    "workspace", "customer", "owner", "industry", "website", "hallucination",
    "push_prompts", "enabled_tools",
)


class SetupState(TypedDict, total=False):
    workspace: str
    customer: str
    owner: str
    industry: str
    website: str
    hallucination: bool
    push_prompts: bool
    enabled_tools: list[str]
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


_builder = StateGraph(SetupState)
_builder.add_node("setup", _run)
_builder.add_edge(START, "setup")
_builder.add_edge("setup", END)
graph = _builder.compile()
