"""The agnostic MCP proxy (`POST /mcp/proxy/{server_id}`).

One route carrying every MCP message the browser sends, and understanding none
of them. The tests that matter are about what it refuses to do: forward to a
server the assistant does not have, and pass this request's own authority
upstream.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

import custom_demo.web.mcp as WM
from custom_demo.runtime.mcp_servers import McpServer
from custom_demo.webapp import app

client = TestClient(app)

SERVER = McpServer(
    id="excalidraw",
    label="Excalidraw",
    url="https://mcp.example/mcp",
    headers={"Authorization": "Bearer s3cret"},
)
MESSAGE = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


@pytest.fixture
def upstream(monkeypatch):
    """A fake upstream that records what reached it."""
    seen: dict[str, Any] = {}

    class FakeResponse:
        status_code = 200
        headers = {
            "content-type": "text/event-stream",
            "mcp-session-id": "sess-1",
            "set-cookie": "x=1",
        }

        async def aiter_raw(self):
            yield b"event: message\n"
            yield b'data: {"result":{}}\n\n'

        async def aclose(self):
            seen["closed"] = True

    class FakeClient:
        def __init__(self, *_a, **_k):
            pass

        def build_request(self, method, url, content=None, headers=None):
            seen.update(method=method, url=url, content=content, headers=dict(headers or {}))
            return object()

        async def send(self, _request, stream=False):
            seen["stream"] = stream
            return FakeResponse()

        async def aclose(self):
            seen["client_closed"] = True

    monkeypatch.setattr(WM.httpx, "AsyncClient", FakeClient)

    async def resolve(_assistant, server_id):
        return SERVER if server_id == SERVER.id else None

    monkeypatch.setattr(WM, "resolve_server", resolve)
    return seen


def test_refuses_a_server_the_assistant_does_not_have(upstream):
    """Addressed by id, so an unconfigured one is simply absent.

    The whole reason this takes an id rather than a URL: a proxy that forwards
    wherever it is pointed is a general-purpose fetcher wearing the
    deployment's network position.
    """
    r = client.post("/mcp/proxy/not-configured?assistant=a1", json=MESSAGE)
    assert r.status_code == 404
    assert "has no MCP server 'not-configured'" in r.json()["error"]
    assert "url" not in upstream


def test_relays_the_body_untouched_and_adds_the_server_s_own_auth(upstream):
    r = client.post(
        "/mcp/proxy/excalidraw?assistant=a1",
        json=MESSAGE,
        headers={"accept": "application/json, text/event-stream", "mcp-session-id": "sess-1"},
    )
    assert r.status_code == 200
    # Bytes, not parsed and re-serialized: the proxy never learns what an MCP
    # message is, which is what lets it carry every method there will ever be.
    assert upstream["content"] == r.request.content
    assert upstream["url"] == SERVER.url
    assert upstream["stream"] is True
    # The token is attached here, so it never has to exist in the browser.
    assert upstream["headers"]["Authorization"] == "Bearer s3cret"
    assert upstream["headers"]["mcp-session-id"] == "sess-1"


def test_does_not_hand_this_request_s_authority_upstream(upstream):
    """A third-party MCP server must not receive the caller's credentials.

    The browser's request carries whatever authenticates it to THIS deployment.
    Forwarding that to someone else's server would hand them a key to us.
    """
    client.post(
        "/mcp/proxy/excalidraw?assistant=a1",
        json=MESSAGE,
        headers={
            "accept": "application/json",
            "cookie": "session=mine",
            "x-api-key": "the-deployment-key",
            "authorization": "Bearer someone-elses",
        },
    )
    sent = {k.lower() for k in upstream["headers"]}
    assert "cookie" not in sent
    assert "x-api-key" not in sent
    # Authorization is present, but it is the SERVER's, not the caller's.
    assert upstream["headers"]["Authorization"] == "Bearer s3cret"


def test_returns_the_stream_and_the_session_but_not_the_upstream_s_cookies(upstream):
    r = client.post("/mcp/proxy/excalidraw?assistant=a1", json=MESSAGE)
    assert r.text == 'event: message\ndata: {"result":{}}\n\n'
    assert r.headers["content-type"] == "text/event-stream"
    # Streamable HTTP keeps a connection by this id, so dropping it would make
    # every message a new session.
    assert r.headers["mcp-session-id"] == "sess-1"
    # A third-party server does not get to set cookies on our origin.
    assert "set-cookie" not in {k.lower() for k in r.headers}


def test_a_dead_server_is_a_502_with_the_reason(monkeypatch):
    class Boom:
        def __init__(self, *_a, **_k):
            pass

        def build_request(self, *_a, **_k):
            return object()

        async def send(self, *_a, **_k):
            raise httpx.ConnectError("nodename nor servname provided")

        async def aclose(self):
            return None

    monkeypatch.setattr(WM.httpx, "AsyncClient", Boom)

    async def resolve(_assistant, _server_id):
        return SERVER

    monkeypatch.setattr(WM, "resolve_server", resolve)
    r = client.post("/mcp/proxy/excalidraw?assistant=a1", json=MESSAGE)
    # The browser is blocked on a JSON-RPC response; a hang looks to the person
    # like a button that does nothing.
    assert r.status_code == 502
    assert "nodename" in r.json()["error"]


@pytest.fixture
def redirector(monkeypatch):
    """An upstream that answers a redirect, then the real thing."""
    seen: dict[str, Any] = {"urls": []}

    class FakeResponse:
        def __init__(self, status, headers, body=b"ok"):
            self.status_code = status
            self.headers = headers
            self._body = body

        async def aiter_raw(self):
            yield self._body

        async def aclose(self):
            return None

    class FakeClient:
        def __init__(self, *_a, **_k):
            pass

        def build_request(self, _method, url, content=None, headers=None):
            seen["urls"].append(url)
            return url

        async def send(self, url, stream=False):
            return seen["answer"](url)

        async def aclose(self):
            return None

    monkeypatch.setattr(WM.httpx, "AsyncClient", FakeClient)

    async def resolve(_assistant, _server_id):
        override = seen.get("resolve")
        return await override(_assistant, _server_id) if override else SERVER

    monkeypatch.setattr(WM, "resolve_server", resolve)
    seen["FakeResponse"] = FakeResponse
    return seen


def test_follows_a_redirect_the_server_issues_to_itself(redirector):
    """A bare MCP hostname commonly answers 308 to its real path.

    `https://mcp.excalidraw.com` sends you to `/mcp`, and httpx does not follow
    by default. Handing that back to the browser reads as "the server refused",
    when the server in fact said where to go.
    """
    R = redirector["FakeResponse"]

    bare = "https://mcp.example"

    async def resolve(_assistant, _server_id):
        return dataclasses.replace(SERVER, url=bare)

    def answer(url):
        if url == bare:
            return R(308, {"location": "/mcp"})

        return R(200, {"content-type": "text/event-stream"}, b"event: message\n")

    redirector["answer"] = answer
    redirector["resolve"] = resolve
    r = client.post("/mcp/proxy/excalidraw?assistant=a1", json=MESSAGE)
    assert r.status_code == 200
    assert redirector["urls"] == [bare, "https://mcp.example/mcp"]


def test_does_not_follow_a_redirect_off_the_configured_host(redirector):
    """Addressing by ID is pointless if the server can then send us anywhere.

    Following off-host would aim the deployment's network position at an
    address nobody configured, so the redirect goes back to the browser
    unfollowed instead.
    """
    R = redirector["FakeResponse"]
    redirector["answer"] = lambda _url: R(308, {"location": "https://elsewhere.invalid/mcp"})

    r = client.post("/mcp/proxy/excalidraw?assistant=a1", json=MESSAGE)
    assert r.status_code == 308
    assert redirector["urls"] == [SERVER.url]


def test_does_not_follow_a_redirect_that_would_drop_the_body(redirector):
    """301, 302 and 303 turn a POST into a GET.

    For MCP that means the message is silently discarded and the browser waits
    for a reply to a request the server never saw, which is worse than a
    visible redirect.
    """
    R = redirector["FakeResponse"]
    redirector["answer"] = lambda _url: R(302, {"location": "/mcp"})

    r = client.post("/mcp/proxy/excalidraw?assistant=a1", json=MESSAGE)
    assert r.status_code == 302
    assert redirector["urls"] == [SERVER.url]
