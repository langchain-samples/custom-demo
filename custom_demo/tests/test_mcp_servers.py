"""MCP server connections: config parsing, caching, and the demo server itself.

Two halves. The first pins the pure config layer (`mcp_servers.py`), which is the
part a bad value in Settings reaches first. The second runs the bundled demo
server in-process over FastMCP's in-memory transport, so the modern-spec
behaviours we depend on (a cacheable tool list, the guard-pattern elicitation
round, the MCP App resource) are verified without a socket or a tunnel.
"""

from __future__ import annotations

import asyncio
import base64
import struct
import zlib
from types import SimpleNamespace
from typing import Literal

import pytest
from fastmcp import Client
from mcp.types import ElicitResult, InputRequiredResult
from starlette.testclient import TestClient

from custom_demo.runtime import mcp_servers as m
from mcp_demo_server.server import mcp

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


def test_app_uri_reads_the_mcp_apps_metadata():
    tool = SimpleNamespace(
        metadata={"mcp": {"tool": {"_meta": {"ui": {"resourceUri": "ui://a/b.html"}}}}}
    )
    assert m.app_uri(tool) == "ui://a/b.html"


def test_app_uri_reads_the_deprecated_flat_key():
    """A server on the extension's earlier spelling still renders its app.

    SEP-1865 deprecates `_meta["ui/resourceUri"]` but keeps it until GA. Ignoring
    it would show a generic form for a server that does ship a UI, with nothing
    anywhere saying why.
    """
    tool = SimpleNamespace(
        metadata={"mcp": {"tool": {"_meta": {"ui/resourceUri": "ui://a/b.html"}}}}
    )
    assert m.app_uri(tool) == "ui://a/b.html"


def test_app_uri_prefers_the_current_key_when_a_server_sends_both():
    tool = SimpleNamespace(
        metadata={
            "mcp": {
                "tool": {
                    "_meta": {
                        "ui": {"resourceUri": "ui://a/new.html"},
                        "ui/resourceUri": "ui://a/old.html",
                    }
                }
            }
        }
    )
    assert m.app_uri(tool) == "ui://a/new.html"


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"mcp": {}},
        {"mcp": {"tool": {"_meta": {}}}},
        # A non-`ui://` URI is not an MCP App, and must not be fetched as one.
        {"mcp": {"tool": {"_meta": {"ui": {"resourceUri": "https://evil/x.html"}}}}},
    ],
)
def test_app_uri_is_none_for_an_ordinary_tool(metadata):
    assert m.app_uri(SimpleNamespace(metadata=metadata)) is None


# ---------------------------------------------------------------------------
# Caching and degradation
# ---------------------------------------------------------------------------


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
        return [SimpleNamespace(name="x_tool")]

    monkeypatch.setattr(m, "_discover", once)
    servers = m.parse_servers([{"label": "Cached", "url": "https://x/mcp"}])
    m.invalidate(servers)

    async def twice():
        return await m.load_tools(servers), await m.load_tools(servers)

    first, second = asyncio.run(twice())
    assert [t.name for t in first] == [t.name for t in second] == ["x_tool"]
    assert calls == 1, "the second load should not have touched the network"
    m.invalidate(servers)


def test_probe_reports_the_reason_a_server_failed(monkeypatch):
    async def boom(*_args, **_kwargs):
        raise ConnectionError("refused")

    monkeypatch.setattr(m, "_discover", boom)
    servers = m.parse_servers([{"label": "Down", "url": "https://nope.invalid/mcp"}])
    (result,) = asyncio.run(m.probe(servers))["servers"]
    assert result["ok"] is False
    assert "refused" in result["error"]


# ---------------------------------------------------------------------------
# The demo server, in-process
# ---------------------------------------------------------------------------


@pytest.fixture
def meridian():
    """The bundled demo MCP server, reached over FastMCP's in-memory transport."""
    return Client(mcp)


def _rounds(
    client,
    tool: str,
    args: dict,
    key: str,
    content: dict | None,
    action: _Action = "accept",
):
    """Both legs of a guard tool: the ask, then the answer. Returns (schema, result).

    Two `call_tool`s rather than one because that IS the pattern: the first leg
    ends the call with an input-required result, and the second re-issues the
    same call with the answer attached. Nothing is held open in between, which is
    what lets a real run put a checkpoint there.
    """

    async def go():
        async with client as c:
            asked = await c.session.call_tool(tool, args, allow_input_required=True)
            assert isinstance(asked, InputRequiredResult), f"{tool} answered without asking"
            answered = await c.session.call_tool(
                tool,
                args,
                input_responses={key: ElicitResult(action=action, content=content)},
                request_state=asked.request_state,
                allow_input_required=True,
            )
            assert not isinstance(answered, InputRequiredResult), f"{tool} asked twice"
            return asked.input_requests[key].params.requested_schema, answered

    return asyncio.run(go())


_BALANCED = {
    "us_equity": 38.0,
    "intl_equity": 17.0,
    "fixed_income": 30.0,
    "alternatives": 10.0,
    "cash": 5.0,
    "approved": True,
}

_SLOT = {
    "review_date": "2026-10-14",
    "window": "morning",
    "attending": "Dana Whitfield, +1 415 555 0134",
}


def test_the_demo_server_advertises_exactly_the_tools_it_has(meridian):
    """One server, one catalogue. A tool that vanishes here breaks a scripted demo."""

    async def names():
        async with meridian as c:
            return {t.name for t in await c.list_tools()}

    assert asyncio.run(names()) == {
        "list_accounts",
        "get_account",
        "schedule_review",
        "propose_rebalance",
        "project_goal",
        "confirm_trade",
        "sign_document",
    }


def test_only_the_tools_that_need_their_own_ui_have_one(meridian):
    """Each App earns its place by collecting what a generated form cannot."""

    async def check():
        async with meridian as c:
            apps = {
                t.name: ((t.meta or {}).get("ui") or {}).get("resourceUri")
                for t in await c.list_tools()
            }
            resources = {str(r.uri): r for r in await c.list_resources()}
            return apps, resources

    apps, resources = asyncio.run(check())
    assert apps["propose_rebalance"] == "ui://meridian/rebalance.html"
    assert apps["project_goal"] == "ui://meridian/projection.html"
    assert apps["confirm_trade"] == "ui://meridian/trade.html"
    assert apps["sign_document"] == "ui://meridian/signature.html"
    # The read-only lookups are ordinary tools; an App there would be decoration.
    assert apps["list_accounts"] is None
    assert apps["get_account"] is None
    # And `schedule_review` is deliberately bare: the guard pattern with no UI on
    # top of it, so the host generates a form from the schema.
    assert apps["schedule_review"] is None

    # The MCP Apps MIME type is what tells a host this is renderable UI rather
    # than a document to read.
    app = resources["ui://meridian/signature.html"]
    assert app.mime_type == "text/html;profile=mcp-app"


def test_a_plain_tool_answers_without_asking(meridian):
    async def call():
        async with meridian as c:
            return await c.call_tool("list_accounts", {})

    data = asyncio.run(call()).data
    assert data["count"] == 2
    whitfield = next(a for a in data["accounts"] if a["account_id"] == "MW-10241")
    assert whitfield["needs_rebalance"] is True


def test_an_unknown_account_is_an_answer_not_a_crash(meridian):
    async def call():
        async with meridian as c:
            return await c.call_tool("get_account", {"account_id": "MW-00000"})

    assert "No account" in asyncio.run(call()).data["error"]


# ---------------------------------------------------------------------------
# The guard pattern with nothing on top of it
# ---------------------------------------------------------------------------


def test_a_tool_with_no_app_still_asks_before_it_answers(meridian):
    """The bare guard-pattern round: ask first, then answer when the reply lands.

    This is the shape the stateless spec requires and the reason the agent can
    surface the pause as an interrupt. `ctx.elicit()` would fail here.
    """
    schema, result = _rounds(meridian, "schedule_review", {"account_id": "MW-10241"}, "slot", _SLOT)
    assert set(schema["properties"]) == {"review_date", "window", "attending"}
    # No App, so no render context to smuggle: a host generates the form itself.
    assert not any("x-app" in prop for prop in schema["properties"].values())

    record = result.structured_content
    assert record["status"] == "scheduled"
    assert record["review_date"] == "2026-10-14"
    assert record["household"] == "Whitfield Family Trust"


@pytest.mark.parametrize(
    ("action", "status"),
    [("decline", "not_scheduled"), ("cancel", "cancelled")],
)
def test_a_refused_ask_books_nothing(meridian, action: _Action, status: str):
    """Declining and cancelling are different answers, and neither is a booking."""
    _, result = _rounds(
        meridian, "schedule_review", {"account_id": "MW-10388"}, "slot", None, action
    )
    assert result.structured_content["status"] == status


# ---------------------------------------------------------------------------
# The MCP Apps
# ---------------------------------------------------------------------------


def test_an_app_gets_its_render_context_on_a_property(meridian):
    """The context has to survive the wire, and only property extras do.

    The SDK strips unknown ROOT keys off `requested_schema`. This is the test
    that catches someone moving the context back there, where it vanishes with
    no error and the app renders empty.
    """
    schema, _ = _rounds(
        meridian, "propose_rebalance", {"account_id": "MW-10241"}, "rebalance", _BALANCED
    )
    assert "x-app" not in schema, "context at the schema root is dropped in transit"
    context = schema["properties"]["approved"]["x-app"]
    assert [s["label"] for s in context["sleeves"]] == [
        "US equity",
        "Intl equity",
        "Fixed income",
        "Alternatives",
        "Cash",
    ]
    assert context["portfolio_value"] == 4_820_000


def test_the_rebalance_schema_is_flat(meridian):
    """One number per sleeve, because elicitation content allows only primitives.

    A nested `allocation` object is rejected by `ElicitResult` before it ever
    reaches the server, so the schema cannot ask for one.
    """
    schema, _ = _rounds(
        meridian, "propose_rebalance", {"account_id": "MW-10241"}, "rebalance", _BALANCED
    )
    kinds = {k: v["type"] for k, v in schema["properties"].items()}
    assert kinds == {
        "us_equity": "number",
        "intl_equity": "number",
        "fixed_income": "number",
        "alternatives": "number",
        "cash": "number",
        "approved": "boolean",
    }


def test_an_approved_rebalance_comes_back_as_trades(meridian):
    _, result = _rounds(
        meridian, "propose_rebalance", {"account_id": "MW-10241"}, "rebalance", _BALANCED
    )
    record = result.structured_content
    assert record["status"] == "approved"
    # Moving every sleeve onto policy leaves nothing to drift.
    assert record["residual_drift"] == 0.0
    sells = {t["sleeve"] for t in record["trades"] if t["side"] == "SELL"}
    assert sells == {"US equity", "Alternatives", "Cash"}
    # Only sales realise a gain, so only sales are taxed.
    assert record["estimated_tax"] > 0


def test_an_allocation_that_is_not_a_portfolio_is_refused(meridian):
    _, result = _rounds(
        meridian,
        "propose_rebalance",
        {"account_id": "MW-10241"},
        "rebalance",
        {**_BALANCED, "us_equity": 80.0},
    )
    assert "not 100%" in result.structured_content["error"]
    assert "Nothing was submitted" in result.structured_content["error"]


def test_a_goal_plan_is_saved_against_the_account(meridian):
    schema, result = _rounds(
        meridian,
        "project_goal",
        {"account_id": "MW-10388"},
        "plan",
        {"retirement_age": 62, "monthly_contribution": 3000, "risk_level": "Growth"},
    )
    context = schema["properties"]["retirement_age"]["x-app"]
    assert context["current_age"] == 45
    assert context["goal"] == 2_000_000
    record = result.structured_content
    assert record["status"] == "saved"
    assert record["years_to_goal"] == 17


def test_a_confirmed_ticket_becomes_an_order(meridian):
    schema, result = _rounds(
        meridian,
        "confirm_trade",
        {"account_id": "MW-10241", "symbol": "AAPL", "side": "sell"},
        "ticket",
        {
            "quantity": 500,
            "order_type": "limit",
            "limit_price": 230.0,
            "time_in_force": "gtc",
            "confirmed": True,
        },
    )
    context = schema["properties"]["quantity"]["x-app"]
    # The ticket needs the position to stop the advisor overselling it.
    assert context["max_qty"] == 4200
    assert context["last"] == 227.14
    order = result.structured_content
    assert order["status"] == "accepted"
    assert order["estimated_principal"] == 500 * 230.0


def test_an_order_cannot_sell_more_than_is_held(meridian):
    """The app blocks it, and so does the server: the app is not the boundary."""
    _, result = _rounds(
        meridian,
        "confirm_trade",
        {"account_id": "MW-10241", "symbol": "AAPL", "side": "sell"},
        "ticket",
        {
            "quantity": 99_999,
            "order_type": "market",
            "limit_price": None,
            "time_in_force": "day",
            "confirmed": True,
        },
    )
    assert "only 4200 held" in result.structured_content["error"]
    assert "Nothing placed" in result.structured_content["error"]


def test_every_app_resource_is_a_complete_document(meridian):
    """A `ui://` that 404s or arrives half-built is a blank iframe on stage."""

    async def read():
        async with meridian as c:
            uris = [str(r.uri) for r in await c.list_resources()]
            return uris, {u: (await c.read_resource(u))[0].text for u in uris}

    uris, docs = asyncio.run(read())
    assert set(uris) == {
        "ui://meridian/rebalance.html",
        "ui://meridian/projection.html",
        "ui://meridian/trade.html",
        "ui://meridian/signature.html",
    }
    for uri, html in docs.items():
        assert html.startswith("<!doctype html>"), uri
        # The bridge is injected, not imported: the iframe has no origin to
        # fetch a script from.
        assert "window.McpApp" in html, uri
        assert "McpApp.ready()" in html, uri


# ---------------------------------------------------------------------------
# The signature: a multimodal result, and the PNG behind it
# ---------------------------------------------------------------------------


def _png(width: int, height: int) -> bytes:
    """A real PNG of the given size, standing in for what the pad draws.

    Generated rather than hard-coded because the SIZE is load-bearing here: a
    provider rejects a tiny image, so a fixture that happened to be 1x1 would
    test the wrong branch.
    """
    raw = b"".join(b"\x00" + bytes([255, 255, 255] * width) for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


# What a signature pad actually hands over: a canvas-sized image.
_PNG_BYTES = _png(480, 200)


def _data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


def _sign(client, document: str, signature: str, action: _Action = "accept"):
    """Both rounds of `sign_document`, with the pad handing over `signature`."""
    _, result = _rounds(
        client,
        "sign_document",
        {"account_id": "MW-10241", "document": document},
        "signature",
        None
        if action != "accept"
        else {
            "signature": signature,
            "signed_by": "Dana Whitfield",
            "signed_at": "2026-09-07T14:02:00Z",
        },
        action,
    )
    return result


def test_a_signature_comes_back_as_an_image_and_a_url(meridian):
    """The model both SEES the signature and gets a way to embed it.

    Two blocks, deliberately: the image is what makes the signature reviewable,
    and the data URI is what makes it placeable in a document with no network at
    all. The URL is the fallback for one too big to inline.
    """
    result = _sign(meridian, "IPS amendment", _data_uri(_PNG_BYTES))

    assert [block.type for block in result.content] == ["text", "image"]
    assert result.content[1].mime_type == "image/png"

    record = result.structured_content
    assert record["status"] == "signed"
    assert record["signed_by"] == "Dana Whitfield"
    assert record["signature_data_uri"].startswith("data:image/png;base64,")
    assert record["signature_url"].endswith(f"/signatures/{record['reference']}.png")
    # The raw column the record is stored under never reaches the model.
    assert "signature" not in record


def test_the_reference_is_the_same_document_twice(meridian):
    """The PNG route is keyed on it, so it cannot be a per-process hash."""
    first = _sign(meridian, "Advisory agreement", _data_uri(_PNG_BYTES))
    second = _sign(meridian, "Advisory agreement", _data_uri(_PNG_BYTES))
    assert first.structured_content["reference"] == second.structured_content["reference"]


def test_a_signature_too_small_to_render_keeps_the_url_and_drops_the_image(meridian):
    """A provider rejects a tiny image with a 400 that would kill the whole run.

    Found the hard way: a 1x1 test PNG came back as "Could not process image"
    from Anthropic and ended the turn after the tool had already succeeded.
    """
    result = _sign(meridian, "Beneficiary change", _data_uri(_png(1, 1)))

    assert [block.type for block in result.content] == ["text"], "the image should be dropped"
    # The document is still signed, and the picture is still fetchable by URL.
    record = result.structured_content
    assert record["status"] == "signed"
    assert record["signature_url"].endswith(f"/signatures/{record['reference']}.png")


def test_a_signature_the_pad_mangled_is_recorded_without_promising_an_image(meridian):
    """A bad data URI must not claim a picture that cannot be served."""
    record = _sign(meridian, "Trading authority", "not-a-data-uri").structured_content
    assert record["status"] == "signed"
    assert record["signature_url"] is None
    assert record["signature_data_uri"] is None


def test_a_declined_signature_leaves_the_document_unsigned(meridian):
    record = _sign(meridian, "Fee schedule", "", action="decline").structured_content
    assert record["status"] == "unsigned"


def test_the_signature_png_is_served_over_http(meridian):
    """The URL in the result resolves to real image bytes, and 404s otherwise."""
    record = _sign(meridian, "Custody transfer", _data_uri(_PNG_BYTES)).structured_content
    with TestClient(mcp.http_app(stateless_http=True)) as http:
        ok = http.get(f"/signatures/{record['reference']}.png")
        assert ok.status_code == 200
        assert ok.headers["content-type"] == "image/png"
        assert ok.content == _PNG_BYTES
        assert http.get("/signatures/MW-DOC-99999.png").status_code == 404
