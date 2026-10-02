"""MCP server connections: config parsing, caching, and discovery fallbacks.

Pins the pure config layer (`mcp_servers.py`), which is the part a bad value in
Settings reaches first, and the discovery path with the network faked out.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Literal

import pytest
from langchain.mcp.apps import filter_model_visible_tools

from custom_demo.runtime import mcp_servers as m

# What a client is allowed to answer an elicitation with. Spelled out rather
# than `str` because `ElicitResult` takes the literal union, and a typo in a
# test would otherwise only show up as a validation error at run time.
_Action = Literal["accept", "decline", "cancel"]

# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------


def test_parse_servers_reads_a_normal_entry():
    (server,) = m.parse_servers([{"label": "Fieldlink", "url": "https://x.ngrok.app/mcp"}])
    assert server.id == "fieldlink"
    assert server.label == "Fieldlink"
    assert server.url == "https://x.ngrok.app/mcp"


def test_parse_servers_turns_a_token_into_a_bearer_header():
    (server,) = m.parse_servers([{"label": "F", "url": "https://x/mcp", "token": "s3cret"}])
    assert server.headers == {"Authorization": "Bearer s3cret"}


def test_redacted_reports_header_names_but_never_values():
    (server,) = m.parse_servers([{"label": "F", "url": "https://x/mcp", "token": "s3cret"}])
    shown = server.redacted()
    assert shown["header_names"] == ["Authorization"]
    assert "s3cret" not in str(shown)


@pytest.mark.parametrize(
    "entry",
    [
        {"label": "no url"},
        {"label": "relative", "url": "/mcp"},
        # A bare path would be read as a subprocess to launch by FastMCP's string
        # inference, which is exactly the case the http(s) guard exists for.
        {"label": "script", "url": "server.py"},
        {"label": "off", "url": "https://x/mcp", "enabled": False},
        "not-a-dict",
    ],
)
def test_parse_servers_skips_anything_it_cannot_connect_to(entry):
    assert m.parse_servers([entry]) == ()


def test_parse_servers_never_lets_two_servers_share_a_namespace():
    servers = m.parse_servers(
        [
            {"label": "Field Link", "url": "https://a/mcp"},
            {"label": "field-link", "url": "https://b/mcp"},
        ]
    )
    assert [s.id for s in servers] == ["field_link", "field_link_2"]


def test_parse_servers_accepts_a_stringified_list():
    (server,) = m.parse_servers('[{"label": "F", "url": "https://x/mcp"}]')
    assert server.url == "https://x/mcp"


def test_parse_servers_shrugs_off_junk():
    assert m.parse_servers(None) == ()
    assert m.parse_servers("not json") == ()
    assert m.parse_servers({"label": "F"}) == ()


def test_fingerprint_changes_with_the_token_so_a_new_key_is_not_served_from_cache():
    a = m.parse_servers([{"label": "F", "url": "https://x/mcp", "token": "one"}])
    b = m.parse_servers([{"label": "F", "url": "https://x/mcp", "token": "two"}])
    assert m.fingerprint(a) != m.fingerprint(b)


def test_fingerprint_is_stable_for_the_same_config():
    entry = [{"label": "F", "url": "https://x/mcp"}]
    assert m.fingerprint(m.parse_servers(entry)) == m.fingerprint(m.parse_servers(entry))


def _tool(name, visibility=None):
    """A discovered tool carrying the MCP provenance the adapter attaches."""
    ui = {} if visibility is None else {"visibility": visibility}
    return SimpleNamespace(name=name, metadata={"mcp": {"tool": {"_meta": {"ui": ui}}}})


@pytest.mark.parametrize(
    ("visibility", "seen"),
    [
        (None, True),
        (["model", "app"], True),
        (["model"], True),
        # The MUST: a tool the server published to its App alone.
        (["app"], False),
        # Junk is not a reason to hide a working tool.
        ("model", True),
    ],
)
def test_model_visible_hides_only_the_app_only_tools(visibility, seen):
    """SEP-1865 forbids putting an app-only tool in the agent's tool list.

    Excalidraw is the live case: `create_view` is for the model, while
    `save_checkpoint` and friends are `visibility: ["app"]`. Handing those to the
    model invites it to call a tool meant for the App's own bookkeeping.

    The rule lives in `langchain.mcp.apps` now. This still tests it, because
    what matters here is that the tools this deployment builds carry the
    metadata that filter reads, in the shape it reads it from.
    """
    tool = _tool("t", visibility)

    assert bool(filter_model_visible_tools([tool])) is seen


def test_instructions_are_cached_beside_the_tools(monkeypatch):
    """One discovery fills both, so guidance costs no extra round trip.

    `awrap_model_call` fires on every model call, so reading `instructions` on
    its own connection would be a round trip per call, which is the thing the
    tools cache exists to avoid.
    """
    calls = 0

    async def once(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return [_tool("srv_thing")], {"srv": "Call get_project before updating one."}

    monkeypatch.setattr(m, "_discover", once)
    servers = m.parse_servers([{"label": "Srv", "url": "https://x/mcp"}])
    m.invalidate(servers)

    asyncio.run(m.load_tools(servers))
    assert m.instructions_for(servers) == {"srv": "Call get_project before updating one."}
    # Served from the same cache, so no second connection.
    asyncio.run(m.load_tools(servers))
    assert calls == 1


def test_instructions_are_empty_for_a_server_with_nothing_to_say(monkeypatch):
    """Most servers publish none, Excalidraw included, and that is not an error."""

    async def quiet(*_args, **_kwargs):
        return [_tool("srv_thing")], {}

    monkeypatch.setattr(m, "_discover", quiet)
    servers = m.parse_servers([{"label": "Srv", "url": "https://y/mcp"}])
    m.invalidate(servers)
    asyncio.run(m.load_tools(servers))
    assert m.instructions_for(servers) == {}


def test_invalidate_drops_instructions_too():
    """Stale guidance outliving a token change would be worse than none."""
    servers = m.parse_servers([{"label": "Srv", "url": "https://z/mcp"}])
    m._INSTRUCTIONS[m.fingerprint(servers)] = {"srv": "old"}
    m.invalidate()
    assert m.instructions_for(servers) == {}


def test_load_tools_returns_nothing_when_no_server_is_configured():
    assert asyncio.run(m.load_tools(())) == []


def test_load_tools_degrades_to_no_tools_when_a_server_is_unreachable(monkeypatch):
    """A dead tunnel must cost the turn its MCP tools, never the whole turn."""

    async def boom(*_args, **_kwargs):
        raise ConnectionError("tunnel is down")

    monkeypatch.setattr(m, "_discover", boom)
    servers = m.parse_servers([{"label": "Down", "url": "https://nope.invalid/mcp"}])
    m.invalidate(servers)
    assert asyncio.run(m.load_tools(servers)) == []


def test_load_tools_serves_a_second_call_from_cache(monkeypatch):
    calls = 0

    async def once(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        # `_discover` returns tools AND what each server said, in one pass.
        return [_tool("x_tool")], {}

    monkeypatch.setattr(m, "_discover", once)
    servers = m.parse_servers([{"label": "Cached", "url": "https://x/mcp"}])
    m.invalidate(servers)

    async def twice():
        return await m.load_tools(servers), await m.load_tools(servers)

    first, second = asyncio.run(twice())
    assert [t.name for t in first] == [t.name for t in second] == ["x_tool"]
    assert calls == 1, "the second load should not have touched the network"
    m.invalidate(servers)


def test_one_unreachable_server_does_not_take_the_others_down(monkeypatch):
    """A dead tunnel must cost its own tools and nobody else's.

    A `ClientGroup` connects every member together and raises as a unit, so an
    expired tunnel in the list used to return zero tools for every server. The
    visible symptom was the agent announcing that a perfectly healthy
    integration was unavailable, and `/mcp/bootstrap` answering with no apps.
    """
    good = m.McpServer(id="excalidraw", label="Excalidraw", url="https://good/mcp")
    dead = m.McpServer(id="everything", label="Everything", url="https://dead/mcp")
    tool = SimpleNamespace(name="excalidraw_create_view", metadata={}, args_schema=None)

    async def fake_discover(servers):
        ids = [s.id for s in servers]
        if "everything" in ids:
            raise RuntimeError("Client failed to connect: nodename nor servname provided")

        return [tool], {"excalidraw": "draw things"}

    monkeypatch.setattr(m, "_discover", fake_discover)
    m._TOOLS.clear()
    out = asyncio.run(m.load_tools((good, dead)))

    # The group pass raises because of `dead`; the per-server retry keeps `good`.
    assert [t.name for t in out] == ["excalidraw_create_view"]
    # And what the healthy server said survives the fallback too, or its
    # guidance would vanish whenever an unrelated server broke.
    assert m.instructions_for((good, dead)) == {"excalidraw": "draw things"}


def test_every_server_unreachable_is_still_an_empty_list(monkeypatch):
    """No tools, never an exception: a broken connection cannot fail a turn."""

    async def fake_discover(servers):
        raise RuntimeError("nope")

    monkeypatch.setattr(m, "_discover", fake_discover)
    m._TOOLS.clear()
    dead = m.McpServer(id="everything", label="Everything", url="https://dead/mcp")
    assert asyncio.run(m.load_tools((dead,))) == []
