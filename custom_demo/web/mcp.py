"""The MCP proxy (`POST /mcp/proxy/{server_id}`).

One route, and it understands no MCP. The SPA holds a real MCP client (see
frontend/src/lib/mcpClients.ts) and this forwards its messages to a server the
assistant is configured for. `initialize`, `tools/list`, `resources/read` and a
view's `tools/call` are all bytes going somewhere; which tools ship a UI, which
are open to apps, and what a `ui://` resolves to are the browser's business and
never reach this file.

Why a proxy at all, when the browser could hold the socket: an MCP server is a
third-party origin that need not send CORS headers, and the bearer token would
have to live in page JavaScript. Claude reaches the same shape at
`/v1/toolbox/shttp/mcp/<connection-uuid>`.
"""

from __future__ import annotations

from urllib.parse import urljoin, urlparse

import httpx
from starlette.responses import JSONResponse, StreamingResponse

from custom_demo.runtime.mcp_servers import resolve_server

# What we pass upstream from the browser's request, and nothing else. Cookies,
# the deployment's own key and anything else on the way in stay here: the only
# authority this request carries upstream is the server's own configured
# headers, added below.
_UP = ("content-type", "accept", "mcp-session-id", "mcp-protocol-version", "last-event-id")
# What comes back. The session id is how Streamable HTTP keeps a connection,
# so dropping it would make every message a new session.
_DOWN = ("content-type", "mcp-session-id", "cache-control")


# Only the two redirects that keep the method and body. A 301, 302 or 303 turns a
# POST into a GET, which for MCP means the message is silently dropped and the
# browser waits for a reply to a request the server never saw.
_KEEPS_METHOD = (307, 308)


async def _send_following_redirects(client, url: str, body: bytes, headers: dict[str, str]):
    """POST to `url`, following a redirect the server issues to itself.

    `httpx` does not follow by default, and a bare MCP hostname commonly
    answers `308` to its real path: `https://mcp.excalidraw.com` sends you to
    `/mcp`. Handing that back to the browser reads as "the server refused",
    when the server in fact said where to go.

    SAME HOST ONLY. Following a redirect off-host would let a configured server
    aim this deployment's network position at an address nobody configured,
    which is the thing addressing by ID is here to prevent.
    """
    for _ in range(3):
        upstream = await client.send(
            client.build_request("POST", url, content=body, headers=headers), stream=True
        )
        location = upstream.headers.get("location")
        if upstream.status_code not in _KEEPS_METHOD or not location:
            return upstream

        target = urljoin(url, location)
        # Off-host, or pointing at itself. A self-redirect is a misconfigured
        # server, and retrying it would spend the hop budget to reach the same
        # answer more slowly.
        if target == url or urlparse(target).netloc != urlparse(url).netloc:
            return upstream

        await upstream.aclose()
        url = target

    return upstream


async def mcp_proxy(request):
    """Forward one MCP message to a configured server, and stream the answer.

    The whole MCP surface the browser needs, in one route that understands none
    of it. It does not parse the body: `initialize`, `tools/list`,
    `resources/read` and a view's `tools/call` are all just bytes going to a
    server the assistant is already connected to. That is the point. Everything
    this deployment used to know about MCP Apps (which tools ship a UI, which
    are open to apps, what a `ui://` resolves to) is the browser's business
    once it holds a real client, and none of it belongs in a proxy.

    A browser cannot hold the connection itself: an MCP server is a third-party
    origin that need not send CORS headers, and the bearer token would have to
    be in page JavaScript. Claude reaches the same shape, at
    `/v1/toolbox/shttp/mcp/<connection-uuid>`.

    Addressed by server ID, never by URL. See `resolve_server`: a proxy that
    forwards wherever it is pointed is a general-purpose fetcher wearing the
    deployment's network position.

    Streams rather than buffers. Streamable HTTP answers `text/event-stream`,
    and a server that reports progress during a long tool call must reach the
    view while it is still running.
    """
    assistant_id = str(request.query_params.get("assistant") or "")
    server_id = request.path_params["server_id"]
    try:
        server = await resolve_server(assistant_id, server_id)
    except LookupError as exc:
        # Named, not swallowed into a bare 404: "no such server" and "that is
        # not an assistant" look identical from the browser and are fixed in
        # completely different places.
        return JSONResponse({"error": str(exc)}, status_code=404)

    if server is None:
        return JSONResponse(
            {"error": f"assistant {assistant_id!r} has no MCP server {server_id!r}"},
            status_code=404,
        )

    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() in _UP}
    headers.update(server.headers or {})

    client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0))
    try:
        upstream = await _send_following_redirects(client, server.url, body, headers)
    except Exception as exc:  # noqa: BLE001 - the browser needs the reason, not a hang
        await client.aclose()
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"[:300]}, status_code=502)

    async def pump():
        # `aiter_raw`, so a `text/event-stream` is relayed byte for byte rather
        # than decoded and reassembled. The client is closed here because it
        # has to outlive this handler.
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        pump(),
        status_code=upstream.status_code,
        headers={k: v for k, v in upstream.headers.items() if k.lower() in _DOWN},
    )
