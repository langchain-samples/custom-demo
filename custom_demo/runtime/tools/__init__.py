"""Tool catalogue for the demo agent.

`registry` is the declarative source of truth for which capabilities exist and
which an assistant may expose; `core` and `simulated` hold the implementations.
"""

from custom_demo.runtime.tools.core import push_widget, widget_sink
from custom_demo.runtime.tools.registry import (
    ALWAYS_ON,
    CATALOGUE_IDS,
    DEFAULT_ENABLED,
    EXPLICIT_ONLY,
    HITL_IDS,
    TOOL_REGISTRY,
    ToolSpec,
    all_tools,
    allowed_tool_names,
    call_limit_middlewares,
    guidance_for,
    is_allowed,
    parse_enabled,
    registry_json,
    subagent_tools,
)

__all__ = [
    "ALWAYS_ON",
    "CATALOGUE_IDS",
    "DEFAULT_ENABLED",
    "EXPLICIT_ONLY",
    "HITL_IDS",
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
    "subagent_tools",
    "widget_sink",
]
