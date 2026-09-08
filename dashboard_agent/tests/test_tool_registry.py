"""Unit tests for the tool catalogue and the ToolSelection filter (no LLM).

The invariants that matter:
  * an assistant with no selection behaves exactly as it did before the catalogue
  * an EMPTY selection is a real choice, not "unset"
  * nothing is force-on: every catalogue tool (incl. push_widget) is toggleable
  * tools outside the catalogue (every deepagents built-in) are never stripped
"""

import json
from types import SimpleNamespace

from dashboard_agent.runtime.agent import ToolSelection
from dashboard_agent.runtime.tools import (
    ALWAYS_ON,
    CATALOGUE_IDS,
    DEFAULT_ENABLED,
    TOOL_REGISTRY,
    all_tools,
    allowed_tool_names,
    guidance_for,
    is_allowed,
    parse_enabled,
    registry_json,
)

SOME_BUILTINS = ["ls", "read_file", "write_file", "edit_file", "glob", "grep", "task"]


# --- registry shape ---------------------------------------------------------


def test_an_assistant_with_no_selection_gets_the_dashboard_and_can_ask():
    """`push_widget` is default-on and `ask_user` is always on, so both apply."""
    assert DEFAULT_ENABLED == {"push_widget", "ask_user"}
    assert allowed_tool_names(None) == {"push_widget", "ask_user"}


def test_only_ask_user_cannot_be_switched_off():
    """Pausing to ask instead of guessing is behaviour, not a capability.

    `push_widget` is default-on but droppable, so a support assistant can answer in
    prose. Everything else is opt-in.
    """
    assert ALWAYS_ON == frozenset({"ask_user"})
    assert "push_widget" in DEFAULT_ENABLED


def test_registry_ids_are_unique_and_match_tool_names():
    ids = [s.id for s in TOOL_REGISTRY]
    assert len(ids) == len(set(ids))
    # The id is what the model sees, so it must equal the tool's own name.
    for spec in TOOL_REGISTRY:
        assert spec.tool.name == spec.id


def test_registry_json_is_serializable_and_has_no_tool_objects():
    payload = registry_json()
    json.dumps(payload)  # must not raise
    assert all("tool" not in row for row in payload)
    assert {r["id"] for r in payload} == CATALOGUE_IDS


def test_all_tools_covers_the_catalogue():
    assert {t.name for t in all_tools()} == CATALOGUE_IDS


# --- parse_enabled ----------------------------------------------------------


def test_unset_is_none_but_empty_is_a_real_choice():
    assert parse_enabled(None) is None
    assert parse_enabled([]) == set()
    assert parse_enabled("") == set()


def test_comma_string_parses():
    assert parse_enabled("draft_email, web_search") == {"draft_email", "web_search"}


# --- allowed_tool_names -----------------------------------------------------


def test_empty_selection_keeps_nothing():
    # Empty = "everything off"; with no always-on tools, that means no catalogue tools.
    # `ask_user` survives an empty selection: it is always on.
    assert allowed_tool_names([]) == {"ask_user"}


def test_selection_drops_unknown_and_adds_no_forced_tools():
    assert allowed_tool_names(["draft_email", "not_a_tool"]) == {"draft_email", "ask_user"}


def test_selection_can_drop_a_default_tool():
    assert "push_widget" not in allowed_tool_names(["web_search"])


# --- is_allowed -------------------------------------------------------------


def test_builtins_and_unknown_names_always_pass():
    allowed = allowed_tool_names([])
    for name in SOME_BUILTINS + ["some_future_builtin", "execute"]:
        assert is_allowed(name, allowed), name


def test_catalogue_names_are_governed():
    allowed = allowed_tool_names(["web_search"])
    assert is_allowed("web_search", allowed)
    assert not is_allowed("draft_email", allowed)


# --- guidance + call limits -------------------------------------------------


def test_guidance_only_for_enabled_tools():
    lines = " ".join(guidance_for(allowed_tool_names(["draft_email"])))
    assert "draft_email" in lines
    assert "web_search" not in lines


# --- ToolSelection middleware ----------------------------------------------


class _Req:
    """Minimal ModelRequest stand-in: tools + a runtime carrying context."""

    def __init__(self, tools, ctx):
        self.tools = tools
        self.runtime = SimpleNamespace(context=ctx)

    def override(self, **kw):
        return _Req(kw.get("tools", self.tools), self.runtime.context)


def _request(ctx):
    # Catalogue tools + built-ins + a dict-shaped tool (request.tools is a union).
    tools = all_tools() + [SimpleNamespace(name=n) for n in SOME_BUILTINS] + [{"name": "future"}]
    return _Req(tools, ctx)


def _names(req):
    return {t.name if hasattr(t, "name") else t["name"] for t in req.tools}


def test_middleware_default_offers_exactly_the_old_set_plus_builtins():
    out = ToolSelection()._apply(_request({}))
    assert _names(out) & CATALOGUE_IDS == DEFAULT_ENABLED
    assert set(SOME_BUILTINS) | {"future"} <= _names(out)


def test_middleware_honours_selection():
    out = ToolSelection()._apply(_request({"enabled_tools": ["draft_email", "web_search"]}))
    assert _names(out) & CATALOGUE_IDS == {"draft_email", "web_search", "ask_user"}


def test_middleware_never_strips_builtins_even_when_all_off():
    out = ToolSelection()._apply(_request({"enabled_tools": []}))
    assert set(SOME_BUILTINS) | {"future"} <= _names(out)
    # Even with everything switched off, the always-on tool stays.
    assert _names(out) & CATALOGUE_IDS == {"ask_user"}


def test_middleware_tolerates_missing_context():
    assert _names(ToolSelection()._apply(_request(None))) & CATALOGUE_IDS == DEFAULT_ENABLED
