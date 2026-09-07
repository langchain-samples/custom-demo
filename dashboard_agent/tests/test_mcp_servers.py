"""MCP server connections: config parsing, caching, and the demo server itself.

Two halves. The first pins the pure config layer (`mcp_servers.py`), which is the
part a bad value in Settings reaches first. The second runs the bundled demo
server in-process over FastMCP's in-memory transport, so the modern-spec
behaviours we depend on (a cacheable tool list, the guard-pattern elicitation
round, the MCP App resource) are verified without a socket or a tunnel.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from dashboard_agent import mcp_servers as m

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
def fieldlink():
    """The demo MCP server, reached over FastMCP's in-memory transport."""
    from fastmcp import Client

    from mcp_demo_server.server import mcp

    return Client(mcp)


def test_the_demo_server_advertises_its_signature_app(fieldlink):
    """`collect_signature` is an MCP App; the plain tools are not."""

    async def check():
        async with fieldlink as client:
            tools = {t.name: t for t in await client.list_tools()}
            resources = {str(r.uri): r for r in await client.list_resources()}
            return tools, resources

    tools, resources = asyncio.run(check())

    assert set(tools) == {
        "find_shipments",
        "get_shipment",
        "schedule_delivery",
        "collect_signature",
    }
    ui = (tools["collect_signature"].meta or {}).get("ui") or {}
    assert ui.get("resourceUri") == "ui://fieldlink/signature.html"
    assert not (tools["find_shipments"].meta or {}).get("ui")

    app = resources["ui://fieldlink/signature.html"]
    # The MCP Apps MIME type is what tells a host this is renderable UI rather
    # than a document to read.
    assert app.mime_type == "text/html;profile=mcp-app"


def test_a_plain_tool_answers_without_asking(fieldlink):
    async def call():
        async with fieldlink as client:
            return await client.call_tool("find_shipments", {"status": "customs_hold"})

    data = asyncio.run(call()).data
    assert data["count"] == 1
    assert data["shipments"][0]["tracking_id"] == "FL-4418"


def test_an_interactive_tool_asks_before_it_answers(fieldlink):
    """The guard-pattern round: ask first, then answer when the reply comes back.

    This is the shape the stateless spec requires and the reason the agent can
    surface the pause as an interrupt. `ctx.elicit()` would fail here.
    """
    from mcp.types import ElicitResult, InputRequiredResult

    async def two_rounds():
        async with fieldlink as client:
            asked = await client.session.call_tool(
                "collect_signature", {"tracking_id": "FL-4501"}, allow_input_required=True
            )
            answered = await client.session.call_tool(
                "collect_signature",
                {"tracking_id": "FL-4501"},
                input_responses={
                    "signature": ElicitResult(
                        action="accept",
                        content={
                            "signature": "data:image/png;base64,iVBORw0KGgo=",
                            "signed_by": "Grace Achieng",
                            "signed_at": "2026-09-07T14:02:00Z",
                        },
                    )
                },
                request_state=asked.request_state,
                allow_input_required=True,
            )
            return asked, answered

    asked, answered = asyncio.run(two_rounds())

    assert isinstance(asked, InputRequiredResult)
    (key,) = asked.input_requests
    assert key == "signature"
    schema = asked.input_requests[key].params.requested_schema
    assert set(schema["properties"]) == {"signature", "signed_by", "signed_at"}

    assert not isinstance(answered, InputRequiredResult)
    record = answered.structured_content
    assert record["status"] == "signed"
    assert record["signed_by"] == "Grace Achieng"
    # The PNG is thousands of useless tokens: it is stored, never returned.
    assert "signature" not in record


def _png(width: int, height: int) -> bytes:
    """A real PNG of the given size, standing in for what the pad draws.

    Generated rather than hard-coded because the SIZE is load-bearing here: a
    provider rejects a tiny image, so a fixture that happened to be 1x1 would
    test the wrong branch.
    """
    import struct
    import zlib

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


def _sign(client, tracking_id: str):
    """Run both rounds of `collect_signature` and return the terminal result."""
    import base64

    from mcp.types import ElicitResult

    uri = "data:image/png;base64," + base64.b64encode(_PNG_BYTES).decode()

    async def go():
        async with client as c:
            asked = await c.session.call_tool(
                "collect_signature", {"tracking_id": tracking_id}, allow_input_required=True
            )
            return await c.session.call_tool(
                "collect_signature",
                {"tracking_id": tracking_id},
                input_responses={
                    "signature": ElicitResult(
                        action="accept",
                        content={
                            "signature": uri,
                            "signed_by": "Grace Achieng",
                            "signed_at": "2026-09-07T14:02:00Z",
                        },
                    )
                },
                request_state=asked.request_state,
                allow_input_required=True,
            )

    return asyncio.run(go())


def test_a_signature_comes_back_as_an_image_and_a_url(fieldlink):
    """The model both SEES the signature and gets a way to embed it.

    Two blocks, deliberately: the image is what makes the signature reviewable,
    and the URL is what makes it placeable in a document. The base64 is in
    neither - a model cannot copy 10KB of it into an `<img>` without corrupting
    it, which is the whole reason the URL exists.
    """
    result = _sign(fieldlink, "FL-4417")

    assert [block.type for block in result.content] == ["text", "image"]
    image = result.content[1]
    assert image.mime_type == "image/png"

    record = result.structured_content
    assert record["status"] == "signed"
    assert record["signature_url"].endswith("/signatures/FL-4417.png")
    # Not the bytes, anywhere the model reads.
    assert "signature" not in record
    assert "base64" not in result.content[0].text


def test_a_signature_too_small_to_render_keeps_the_url_and_drops_the_image(fieldlink):
    """A provider rejects a tiny image with a 400 that would kill the whole run.

    Found the hard way: a 1x1 test PNG came back as "Could not process image"
    from Anthropic and ended the turn after the tool had already succeeded.
    """
    import base64

    from mcp.types import ElicitResult

    uri = "data:image/png;base64," + base64.b64encode(_png(1, 1)).decode()

    async def go():
        async with fieldlink as c:
            asked = await c.session.call_tool(
                "collect_signature", {"tracking_id": "FL-4418"}, allow_input_required=True
            )
            return await c.session.call_tool(
                "collect_signature",
                {"tracking_id": "FL-4418"},
                input_responses={
                    "signature": ElicitResult(
                        action="accept",
                        content={
                            "signature": uri,
                            "signed_by": "Grace Achieng",
                            "signed_at": "2026-09-07T14:02:00Z",
                        },
                    )
                },
                request_state=asked.request_state,
                allow_input_required=True,
            )

    result = asyncio.run(go())
    assert [block.type for block in result.content] == ["text"], "the image should be dropped"
    # The delivery is still signed, and the picture is still fetchable by URL.
    assert result.structured_content["status"] == "signed"
    assert result.structured_content["signature_url"].endswith("/signatures/FL-4418.png")


def test_the_signature_png_is_served_over_http(fieldlink):
    """The URL in the result resolves to real image bytes, and 404s otherwise."""
    from starlette.testclient import TestClient

    from mcp_demo_server.server import mcp

    _sign(fieldlink, "FL-4502")
    with TestClient(mcp.http_app(stateless_http=True)) as http:
        ok = http.get("/signatures/FL-4502.png")
        assert ok.status_code == 200
        assert ok.headers["content-type"] == "image/png"
        assert ok.content == _PNG_BYTES
        assert http.get("/signatures/FL-9999.png").status_code == 404


def test_a_signature_the_pad_mangled_is_recorded_without_promising_an_image(fieldlink):
    """A bad data URI must not claim a picture that cannot be served."""
    from mcp.types import ElicitResult

    async def go():
        async with fieldlink as c:
            asked = await c.session.call_tool(
                "collect_signature", {"tracking_id": "FL-4390"}, allow_input_required=True
            )
            return await c.session.call_tool(
                "collect_signature",
                {"tracking_id": "FL-4390"},
                input_responses={
                    "signature": ElicitResult(
                        action="accept",
                        content={
                            "signature": "not-a-data-uri",
                            "signed_by": "Grace Achieng",
                            "signed_at": "2026-09-07T14:02:00Z",
                        },
                    )
                },
                request_state=asked.request_state,
                allow_input_required=True,
            )

    record = asyncio.run(go()).structured_content
    assert record["status"] == "signed"
    assert record["signature_url"] is None


def test_a_declined_signature_leaves_the_shipment_unsigned(fieldlink):
    from mcp.types import ElicitResult

    async def decline():
        async with fieldlink as client:
            asked = await client.session.call_tool(
                "collect_signature", {"tracking_id": "FL-4417"}, allow_input_required=True
            )
            return await client.session.call_tool(
                "collect_signature",
                {"tracking_id": "FL-4417"},
                input_responses={"signature": ElicitResult(action="decline")},
                request_state=asked.request_state,
                allow_input_required=True,
            )

    record = asyncio.run(decline()).structured_content
    assert record["status"] == "unsigned"
