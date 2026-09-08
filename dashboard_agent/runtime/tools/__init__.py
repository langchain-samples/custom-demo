"""Tool catalogue for the demo agent.

`registry` is the declarative source of truth for which capabilities exist and
which an assistant may expose; `core` and `simulated` hold the implementations.
"""

from dashboard_agent.runtime.tools.core import push_widget, widget_sink
from dashboard_agent.runtime.tools.registry import (
    ALWAYS_ON,
    CATALOGUE_IDS,
    DEFAULT_ENABLED,
    EXPLICIT_ONLY,
    TOOL_REGISTRY,
    ToolSpec,
    all_tools,
    allowed_tool_names,
    call_limit_middlewares,
    guidance_for,
    is_allowed,
    parse_enabled,
    registry_json,
)

__all__ = [
    "ALWAYS_ON",
    "CATALOGUE_IDS",
    "DEFAULT_ENABLED",
    "EXPLICIT_ONLY",
    "TOOL_REGISTRY",
    "ToolSpec",
    "all_tools",
    "allowed_tool_names",
    "call_limit_middlewares",
    "guidance_for",
    "is_allowed",
    "parse_enabled",
    "push_widget",
    "registry_json",
    "widget_sink",
]
