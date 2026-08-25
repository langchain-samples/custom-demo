"""Agent Server entrypoint.

`langgraph.json` points at `dashboard_agent/graph.py:graph`. Agent Server imports
this module and serves the graph with its own persistence (so it's built without a
checkpointer). Demo variants are modeled as **assistants** — configuration
instances that set the `Context` fields (prompt/dataset/model) at runtime; see
scripts/seed_assistants.py.

`graph` is a **factory**: Agent Server calls it with each run's config, so we can
read `configurable.ls_workspace` / `ls_project` and route that run's LangSmith
traces to a chosen workspace/project via `tracing_context`. It also reads
`langsmith-trace` (the distributed-tracing header Agent Server surfaces into
`configurable`) so a run started inside someone else's span nests under it rather
than starting its own trace — see voice_trace.py. The compiled graph itself is built
once (`base_graph`) and reused — the factory only wraps the run in a tracing context.
"""

from __future__ import annotations

import contextlib
import os
from typing import Any

from langsmith import Client, tracing_context

# Absolute import: Agent Server loads this entrypoint as a top-level module (no
# package parent), so a relative `from .agent` import would fail here.
from dashboard_agent.agent import build_graph

base_graph = build_graph()

_client_cache: dict[str, Client] = {}


def _routing_key() -> str | None:
    """Key used to build workspace-scoped clients.

    Prefer an explicit cross-workspace key; otherwise the default LangSmith key —
    which works for cross-workspace routing when it's org-scoped (e.g. a personal
    access token).
    """
    return os.getenv("LS_CROSS_WORKSPACE_KEY") or os.getenv("LANGSMITH_API_KEY")


def _client_for_workspace(workspace_id: str) -> Client | None:
    """Cached LangSmith client scoped to a workspace.

    Returns None when no usable key is available (then traces stay in the default
    workspace).
    """
    key = _routing_key()
    if not key:
        return None
    client = _client_cache.get(workspace_id)
    if client is None:
        api_url = os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
        client = Client(api_key=key, api_url=api_url, workspace_id=workspace_id)
        _client_cache[workspace_id] = client
    return client


@contextlib.asynccontextmanager
async def graph(config: Any):
    """Route this run's traces to a chosen workspace/project, then yield the graph.

    Reads `ls_workspace` / `ls_project` from `config["configurable"]`. The SPA sends
    these in the run's `context` (LangGraph surfaces context into `configurable`),
    which avoids the "can't set both context and configurable" error. `ls_workspace`
    needs an org-scoped key to switch tenants; `ls_project` is the project name.
    Both optional — with neither set we yield the graph unwrapped (default behavior).
    """
    configurable = (config or {}).get("configurable", {}) or {}
    workspace_id = configurable.get("ls_workspace") or None
    project_name = configurable.get("ls_project") or None
    # NO DISTRIBUTED-TRACING PARENT HERE, deliberately, and it is worth reading why before
    # adding one back. Voice mode wants the agent run nested inside its `invoke_deep_agent`
    # span (see voice_trace.py), and LangSmith documents exactly that: send `langsmith-trace`,
    # read it off `configurable`, wrap the run in `tracing_context(parent=...)`.
    #
    # Measured on Agent Server 0.11.1 AND 0.13.0, and it fails in TWO different ways depending
    # on what the parent handle points at:
    #
    #   - A ROOT-level parent (the documented shape: a traced Python caller using the SDK or
    #     RemoteGraph with `rt.to_headers()`) keeps the run and propagates the TRACE ID, but
    #     not the parentage: the run lands in the caller's trace as a SECOND ROOT.
    #   - A NON-ROOT parent - which is what voice mode needs, since the span to nest under is
    #     the `invoke_deep_agent` tool span - loses the run entirely. Not nested, not at its
    #     own root, not anywhere, and nothing errors, because ingestion is asynchronous.
    #
    # A control run with the header removed traces normally, full tree, and the same handle
    # nests correctly when the child is a plain `@traceable` in one process. So the mechanism
    # works; what does not is Agent Server's run identity deferring to an inbound parent.
    #
    # An unnested trace is a cosmetic loss. A missing trace is the demo. So the voice shell
    # records the agent run's id on its tool span instead (`closeToolSpan`), which gives a
    # click-through without putting the trace at risk.
    if not workspace_id and not project_name:
        yield base_graph
        return

    client = _client_for_workspace(workspace_id) if workspace_id else None
    with tracing_context(enabled=True, client=client, project_name=project_name):
        yield base_graph
