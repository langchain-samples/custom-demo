"""The tool catalogue: which capabilities an assistant can expose.

One declarative table (`TOOL_REGISTRY`) is the source of truth for the settings
UI, the run-time filter, and the per-tool call limits. Adding a capability means
writing the tool and adding a row here — no changes to `agent.py`, `webapp.py`,
or the frontend list.

Scope: this catalogue governs ONLY the tools it declares. Every deepagents
built-in (`write_todos`, the filesystem tools, `task`, …) is deliberately left
alone — `is_allowed()` passes through any name the catalogue does not know, so a
deepagents upgrade that adds a tool can never have it silently stripped.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain_core.tools import BaseTool

from .core import datasearch, push_widget
from .simulated import draft_email, list_data_sources, suggest_meeting_times, web_search


@dataclass(frozen=True)
class ToolSpec:
    """One selectable capability."""

    id: str            # the exact tool name the model sees
    label: str         # settings-UI label
    description: str   # one-line help under the toggle
    group: str         # settings-UI grouping
    tool: BaseTool
    always_on: bool = False       # cannot be switched off
    default_on: bool = False      # enabled when an assistant has no saved selection
    run_limit: int | None = None  # per-run call cap, enforced by middleware
    guidance: str = ""            # appended to the system prompt when enabled


TOOL_REGISTRY: tuple[ToolSpec, ...] = (
    ToolSpec(
        id="push_widget",
        label="Build dashboard widgets",
        description="Render KPI cards, charts and tables onto the live dashboard canvas.",
        group="Dashboard",
        tool=push_widget,
        always_on=True,
        guidance="Use `push_widget` to build the dashboard.",
    ),
    ToolSpec(
        id="datasearch",
        label="Search data",
        description="Retrieve grounded report excerpts and structured figures.",
        group="Data",
        tool=datasearch,
        default_on=True,
        # One search per run: extra searches let the agent wander to adjacent
        # queries when the asked-for data is missing, which masks the planted gap
        # in the hallucination demo.
        run_limit=1,
        guidance="Use `datasearch` to retrieve grounded figures before answering.",
    ),
    ToolSpec(
        id="list_data_sources",
        label="List connected data sources",
        description="Show the systems behind the answer (CRM, warehouse, ticketing…).",
        group="Data",
        tool=list_data_sources,
        guidance=(
            "Use `list_data_sources` when the user asks what data you can see or "
            "where the numbers come from."
        ),
    ),
    ToolSpec(
        id="draft_email",
        label="Draft an email",
        description="Compose a ready-to-send email from what the data shows.",
        group="Comms",
        tool=draft_email,
        guidance=(
            "Use `draft_email` when the user wants to communicate a finding. Do not "
            "repeat the drafted email in your written answer — it is already shown."
        ),
    ),
    ToolSpec(
        id="suggest_meeting_times",
        label="Suggest meeting times",
        description="Propose plausible meeting slots to follow up on a finding.",
        group="Comms",
        tool=suggest_meeting_times,
        guidance=(
            "Use `suggest_meeting_times` when a finding warrants a follow-up "
            "conversation. Do not list the slots again in your written answer."
        ),
    ),
    ToolSpec(
        id="web_search",
        label="Browse the web",
        description="Look up external context and cite sources.",
        group="Research",
        tool=web_search,
        guidance=(
            "Use `web_search` for context that would not be in internal data "
            "(market conditions, competitors, public news). Cite what you use."
        ),
    ),
)

CATALOGUE_IDS: frozenset[str] = frozenset(s.id for s in TOOL_REGISTRY)
ALWAYS_ON: frozenset[str] = frozenset(s.id for s in TOOL_REGISTRY if s.always_on)
DEFAULT_ENABLED: frozenset[str] = frozenset(
    s.id for s in TOOL_REGISTRY if s.default_on or s.always_on
)

_BY_ID: dict[str, ToolSpec] = {s.id: s for s in TOOL_REGISTRY}


def all_tools() -> list[BaseTool]:
    """Every catalogue tool. Registered once at graph-build time; the selection
    middleware hides the unselected ones per run."""
    return [s.tool for s in TOOL_REGISTRY]


def registry_json() -> list[dict[str, Any]]:
    """The catalogue as JSON for `GET /tools` (no tool objects)."""
    return [
        {
            "id": s.id,
            "label": s.label,
            "description": s.description,
            "group": s.group,
            "always_on": s.always_on,
            "default_on": s.default_on,
        }
        for s in TOOL_REGISTRY
    ]


def parse_enabled(raw: Any) -> set[str] | None:
    """Normalize an `enabled_tools` context value.

    Returns None only when genuinely UNSET. An empty list is a real choice
    ("everything optional off") and returns an empty set — collapsing it to None
    would silently hand the user the defaults back.

    Accepts a list/tuple/set, or a comma-separated string as insurance in case a
    transport layer stringifies the field.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return set()
        return {part.strip() for part in stripped.split(",") if part.strip()}
    if isinstance(raw, (list, tuple, set, frozenset)):
        return {str(x).strip() for x in raw if str(x).strip()}
    return None


def allowed_tool_names(raw: Any) -> set[str]:
    """Resolve the catalogue tools this run may use.

    Unset → `DEFAULT_ENABLED`, which is exactly the pre-catalogue behaviour
    (`datasearch` + `push_widget`), so assistants created before this feature are
    unaffected. Otherwise: the selection, narrowed to known ids, plus the
    always-on ones — enforced here, server-side, not just in the UI.
    """
    parsed = parse_enabled(raw)
    if parsed is None:
        return set(DEFAULT_ENABLED)
    return (parsed & CATALOGUE_IDS) | set(ALWAYS_ON)


def is_allowed(name: str | None, allowed: set[str]) -> bool:
    """Whether a tool may be offered. Names outside the catalogue always pass —
    that is what keeps the deepagents built-ins untouched."""
    if not name or name not in CATALOGUE_IDS:
        return True
    return name in allowed


def guidance_for(allowed: Iterable[str]) -> list[str]:
    """Prompt lines for the enabled capabilities, in catalogue order."""
    allowed = set(allowed)
    return [s.guidance for s in TOOL_REGISTRY if s.id in allowed and s.guidance]


def call_limit_middlewares() -> list[ToolCallLimitMiddleware]:
    """A per-run cap for every spec that declares one. Each instance is inert
    when its tool is not offered, so these are safe to always install."""
    return [
        ToolCallLimitMiddleware(
            tool_name=s.id, run_limit=s.run_limit, exit_behavior="continue"
        )
        for s in TOOL_REGISTRY
        if s.run_limit
    ]
