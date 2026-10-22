"""Managed Deep Agents entry: the `dashboard_agent` deep agent, hosted by `mda deploy`.

The same agent `custom_demo/graph.py` registers on the LangSmith deployment, built from
the same `agent_parts`. MDA owns the backend, checkpointer and store; this file only
decides, per run, whether the thread gets a sandbox. Everything per-assistant (prompt,
skills repo, model, tools, MCP servers) is still read from the run's `context` by the
middleware, exactly as on the LangSmith deployment.

`name` becomes the graph id `dashboard-agent` (MDA turns `_` into `-`), so assistants on
the MDA deployment are bound to that id, not to the LangSmith deployment's
`dashboard_agent`. See `scripts/migrate_assistants_to_mda.py`.
"""

from __future__ import annotations

from typing import Any

from managed_deepagents import ManagedServerRuntime, define_deep_agent

from custom_demo.config import sandbox_enabled
from custom_demo.core.ctx import Context
from custom_demo.runtime.agent import agent_parts
from custom_demo.runtime.backends import hub_owns_filesystem

# Built once per process. The factory runs at the start of every run, on resume and on
# schema reads, so it must not rebuild the model, tools or middleware each time.
_PARTS = agent_parts(managed=True)


def _wants_sandbox(ctx: Context) -> bool:
    """Whether this run's thread gets the declared sandbox.

    Off when the deployment disables sandboxes, and off for a legacy assistant whose
    whole filesystem is its Context Hub agent repo (`hub_owns_filesystem`), which runs
    without a VM on the LangSmith deployment too.
    """
    return sandbox_enabled() and not hub_owns_filesystem(ctx)


def agent(runtime: ManagedServerRuntime[Context]):
    """Select this run's sandbox and return the agent definition.

    Leaving `sandbox` unset keeps the declared `sandbox/` and its plain per-thread name,
    which is what lets the LangSmith deployment's file routes find a thread's box (see
    `custom_demo/resources/mda_sandbox.py`). `None` disables it for the run.
    """
    execution = runtime.execution_runtime
    raw = execution.context if execution is not None else None
    ctx = raw if isinstance(raw, Context) else Context(**(raw or {}))
    selection: dict[str, Any] = {} if _wants_sandbox(ctx) else {"sandbox": None}

    return define_deep_agent(
        name="dashboard_agent",
        model=_PARTS.model,
        tools=_PARTS.tools,
        middleware=_PARTS.middleware,
        subagents=_PARTS.subagents,
        context_schema=Context,
        **selection,
    )
