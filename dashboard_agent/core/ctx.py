"""Reading per-run configuration off a LangGraph runtime.

The same `Context` (see `agent.py`) reaches us in two shapes: a dataclass for
local in-process runs, and a plain dict on the Agent Server deployment (which
also lets through keys that are not `Context` fields, e.g. `ls_project`). Tools
and middleware both need to read it, so the accessor lives here rather than in
`agent.py` — that keeps `dashboard_agent.tools` importable without a cycle.
"""

from __future__ import annotations

from typing import Any


def ctx_get(runtime: Any, field: str) -> Any:
    """Read a Context field off a tool/middleware runtime, tolerant of shape."""
    context = getattr(runtime, "context", None)
    if context is None:
        return None
    if isinstance(context, dict):
        return context.get(field)
    return getattr(context, field, None)
