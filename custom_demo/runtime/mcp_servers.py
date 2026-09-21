"""Connect an assistant to remote MCP servers and adapt their tools.

An assistant names its servers in `context.mcp_servers`; this module turns that
list into LangChain tools the agent can call, and answers the two questions the
SPA asks about a server (does it connect, and does a paused tool ship its own
UI).

Three things are worth knowing before changing anything here.

**Why the tools are loaded per run and not at graph build.** Which servers exist
is per-assistant configuration, and `create_deep_agent(tools=…)` is fixed when
the graph is built. So `McpTools` in `agent.py` adds them to `request.tools` at
model-call time and hands the tool object back at execution time. That is only
viable because the loads are cached here: `awrap_model_call` fires on every model
call, and a network round trip per call would be visible on stage.

**Two caches, doing different jobs.** `Client(cache=True)` is the client-side
`tools/list` cache the modern spec added (SEP-2549): it honours the server's own
TTL hint, and it lives inside FastMCP. `_TOOLS` is ours, and it caches the
*adapted LangChain tool objects* so a warm model call touches no I/O at all,
not even the cache lookup. Both are keyed so that editing a server's URL or token
in Settings takes effect on the next turn rather than the next deploy.

**Why every server is a `ClientGroup`, even a single one.** A group namespaces
each tool as `{server_id}_{tool}`. That is not cosmetic: an MCP server offering a
tool called `push_widget` would otherwise collide with our catalogue, and
`ToolSelection` would filter the MCP one out as an unselected catalogue tool. The
prefix keeps every remote name outside the catalogue's namespace by construction,
and it shows the presenter which server a tool came from.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from custom_demo.config import mcp_timeout_seconds, mcp_tools_ttl_seconds

# Tool discovery is cached for this long. The client-side cache already honours
# the server's TTL hint; this one only avoids re-adapting tools we hold.
TOOLS_TTL_SECONDS = mcp_tools_ttl_seconds()

# A server that has gone away must not hang a turn. Every connection this module
# opens, on discovery and on a tool call, is bounded by this.
CONNECT_TIMEOUT_SECONDS = mcp_timeout_seconds()

_SLUG = re.compile(r"[^a-z0-9]+")

# Each server set's `instructions`, by the same fingerprint the tools are cached
# under, so editing a URL or a token re-reads both together.
_INSTRUCTIONS: dict[str, dict[str, str]] = {}


@dataclass(frozen=True)
class McpServer:
    """One MCP server an assistant is connected to."""

    id: str  # namespace for its tools: `{id}_{tool}`
    label: str  # what Settings shows
    url: str  # the streamable-HTTP endpoint, e.g. https://x.ngrok.app/mcp
    headers: dict[str, str] = field(default_factory=dict)

    def redacted(self) -> dict[str, Any]:
        """The server as the SPA may see it: names of headers, never values."""
        return {
            "id": self.id,
            "label": self.label,
            "url": self.url,
            "header_names": sorted(self.headers),
        }


def slugify(raw: str) -> str:
    """A safe tool-name prefix: lowercase, underscore-separated, never empty."""
    slug = _SLUG.sub("_", (raw or "").strip().lower()).strip("_")
    return slug or "mcp"


def parse_servers(raw: Any) -> tuple[McpServer, ...]:
    """Normalize `context.mcp_servers` into servers we can actually connect to.

    Skips anything malformed rather than raising: a half-typed URL saved in
    Settings must not take the whole agent down. The SPA checks a server properly
    from the browser, with its own MCP client, and shows the result in Settings.

    Accepts a list of dicts, or a JSON string of one, as insurance against a
    transport that stringifies the field.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return ()

    if not isinstance(raw, (list, tuple)):
        return ()

    servers: list[McpServer] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue

        url = str(entry.get("url") or "").strip()
        # Only http(s). A bare string target in FastMCP resolves as a filesystem
        # path before a URL, so a value from config could otherwise select a
        # subprocess; we never pass a bare string, but the check is the boundary.
        if not url.lower().startswith(("http://", "https://")):
            continue

        if entry.get("enabled") is False:
            continue

        label = str(entry.get("label") or entry.get("name") or "").strip()
        base = slugify(str(entry.get("id") or label or url))
        # Two servers sharing a slug would namespace their tools into each other.
        ident = base
        suffix = 2
        while ident in seen:
            ident = f"{base}_{suffix}"
            suffix += 1

        seen.add(ident)

        headers = {
            str(k): str(v)
            for k, v in (entry.get("headers") or {}).items()
            if str(k).strip() and str(v).strip()
        }
        # A tunnel is public, so a token is the usual shape. Accept the plain
        # field the Settings form offers and turn it into the header.
        if token := str(entry.get("token") or "").strip():
            headers.setdefault("Authorization", f"Bearer {token}")

        servers.append(McpServer(id=ident, label=label or ident, url=url, headers=headers))

    return tuple(servers)


def fingerprint(servers: tuple[McpServer, ...]) -> str:
    """A cache key that changes whenever a URL, token or id does."""
    material = json.dumps(
        [[s.id, s.url, sorted(s.headers.items())] for s in servers], sort_keys=True
    )
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def build_group(servers: tuple[McpServer, ...]):
    """A `ClientGroup` over `servers`, each client caching its own tool list.

    `mode="auto"` lets FastMCP negotiate: a modern server gets the stateless
    protocol (and with it cacheable discovery and interrupt-driven elicitation),
    one that has not upgraded still connects over the handshake era.

    The fastmcp imports are function-local on purpose: fastmcp pulls the whole `mcp`
    client stack (~300ms), and this module is on the graph's import path, so a
    deployment with no MCP server configured would pay that at every cold start.
    """
    # Local (see the docstring): keeps the mcp client stack off graph load.
    from fastmcp import Client  # noqa: PLC0415
    from fastmcp.client.group import ClientGroup  # noqa: PLC0415
    from fastmcp.client.transports import StreamableHttpTransport  # noqa: PLC0415
    from langchain.mcp.apps import MCP_APPS_EXTENSION  # noqa: PLC0415

    return ClientGroup(
        {
            s.id: Client(
                StreamableHttpTransport(s.url, headers=s.headers or None),
                # Says this host renders MCP Apps. A server that gates
                # `_meta.ui` on the capability sends none of it otherwise, and
                # every app-only tool then reads as visible to the model.
                extensions=[MCP_APPS_EXTENSION],
                cache=True,
                mode="auto",
                timeout=CONNECT_TIMEOUT_SECONDS,
                init_timeout=CONNECT_TIMEOUT_SECONDS,
            )
            for s in servers
        }
    )


# fingerprint -> (expires_at, tools). Process-wide, like the sandbox cache: the
# webapp and the graph share one process, so a probe warms the agent's path too.
_TOOLS: dict[str, tuple[float, list[Any]]] = {}
_LOCKS: dict[str, asyncio.Lock] = {}


async def load_tools(servers: tuple[McpServer, ...]) -> list[Any]:
    """Adapted LangChain tools for `servers`, cached for `TOOLS_TTL_SECONDS`.

    App-only tools are dropped: the agent must not see them, and that filter,
    `langchain.mcp.apps.filter_model_visible_tools`, is the ONE thing about MCP
    Apps this deployment still knows. Everything else, which tools ship a UI and
    which are open to apps, is the browser's business now that it holds its own
    MCP client.
    The cache holds every tool the server published and the filter runs on the
    way out.

    Never raises: a server that is down, tunnelled to nothing, or refusing the
    token yields no tools and leaves the rest of the agent working. Settings is
    where a connection problem is meant to be visible, from the browser's own
    check; a chat turn is not.
    """
    if not servers:
        return []

    # Local, like the fastmcp imports above and for the same reason: importing
    # `langchain.mcp.apps` runs `langchain.mcp.__init__`, which pulls the
    # adapter and with it the whole mcp client stack. Measured at about
    # 1,000ms, and this module is on the graph's import path.
    from langchain.mcp.apps import filter_model_visible_tools  # noqa: PLC0415

    key = fingerprint(servers)
    now = time.monotonic()
    hit = _TOOLS.get(key)
    if hit and hit[0] > now:
        return filter_model_visible_tools(hit[1])

    lock = _LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        # A second caller that queued on the lock while the first was loading
        # should use what it produced, not load again.
        hit = _TOOLS.get(key)
        if hit and hit[0] > time.monotonic():
            return filter_model_visible_tools(hit[1])

        try:
            tools, said = await asyncio.wait_for(
                _discover(servers),
                timeout=CONNECT_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 - a bad server degrades the turn, never fails it
            # One group, one connection, and therefore one failure for all of
            # them: a single server whose DNS is gone or that never answers
            # takes every other server's tools down with it. Retry one at a
            # time so the damage is limited to the server that caused it.
            _log(f"group discovery failed, retrying per server: {type(exc).__name__}: {exc}")
            tools, said = await _discover_each(servers)

        _INSTRUCTIONS[key] = said
        if not tools:
            # Cache the emptiness briefly, so an unreachable server is not
            # retried (and re-timed-out) on every single model call in a turn.
            _TOOLS[key] = (time.monotonic() + min(TOOLS_TTL_SECONDS, 30.0), [])
            return []

        _TOOLS[key] = (time.monotonic() + TOOLS_TTL_SECONDS, tools)
        return filter_model_visible_tools(tools)


async def _discover_each(servers: tuple[McpServer, ...]) -> tuple[list[Any], dict[str, str]]:
    """Discover each server alone, so one broken server costs only its own tools.

    The fallback for when the single-group pass fails. A `ClientGroup` connects
    every member together and raises as a unit, which is right when everything
    works and catastrophic when one member is a tunnel that has expired: the
    agent loses every MCP tool it had and says the integration is unavailable,
    naming a server that is perfectly healthy.

    Concurrent, so a server that hangs costs one timeout rather than one per
    server. A one-member group still prefixes its tools with the server id, so a
    tool name identifies the server it came from however many are configured.
    """

    async def one(server: McpServer) -> tuple[list[Any], dict[str, str]]:
        try:
            return await asyncio.wait_for(_discover((server,)), timeout=CONNECT_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 - name the server and keep the others
            _log(f"{server.id} is unreachable, its tools are missing: {type(exc).__name__}: {exc}")
            return [], {}

    found = await asyncio.gather(*(one(server) for server in servers))
    return [tool for tools, _ in found for tool in tools], {
        sid: text for _, said in found for sid, text in said.items()
    }


async def _discover(servers: tuple[McpServer, ...]) -> tuple[list[Any], dict[str, str]]:
    """One real discovery pass: connect, list, adapt, and read what each server says.

    Both results come from the same connection on purpose. `awrap_model_call`
    fires on every model call, so a second pass just for `instructions` would be
    a round trip per call, which is the thing this module exists to avoid.
    """
    # Local like `build_group`'s: keeps the mcp client stack off graph load.
    from langchain.mcp import MCPAdapter  # noqa: PLC0415

    group = build_group(servers)
    # The group is entered HERE, with the adapter working inside it, and that
    # nesting is load-bearing: `MCPAdapter(group)` alone leaves
    # `group.clients[...].instructions` empty, so the guidance would silently be
    # lost while the tools came back fine. One connection still, not two.
    async with group, MCPAdapter(group) as adapter:
        # `use` reads the client-side cache while the server's own TTL hint says
        # it is still fresh, so a warm list costs no round trip.
        tools = await adapter.list_tools(cache_mode="use")
        # `instructions` is declared on the client, and `clients` is keyed by the
        # id we namespaced the tools with, so the two line up by construction.
        said = {
            str(sid): client.instructions.strip()
            for sid, client in group.clients.items()
            if isinstance(client.instructions, str) and client.instructions.strip()
        }

    return tools, said


def instructions_for(servers: tuple[McpServer, ...]) -> dict[str, str]:
    """Each server's `instructions`, by server id, from the last discovery.

    A server returns this in its initialize result, and it is the only place it
    can say how its tools relate to each other: a tool description covers one
    tool, while "call get_project before updating one" or "these four open a UI
    and do no work" is a statement about the set. A host that drops it makes the
    server work around it, which is why Excalidraw ships a `read_me` tool.

    Reads the cache `load_tools` filled, so it costs nothing and must be called
    after it. Empty until then, and empty for a server with nothing to say,
    which is most of them.
    """
    return dict(_INSTRUCTIONS.get(fingerprint(servers), {}))


def invalidate(servers: tuple[McpServer, ...] | None = None) -> None:
    """Drop cached tools and instructions, for one server set or all of them.

    The cache's only other exit is `TOOLS_TTL_SECONDS` expiring, so this is what
    a caller uses to make the next `load_tools` go back to the server. Nothing in
    the deployment calls it today: Settings checks a server from the browser with
    its own client, and a server whose id, URL or headers change lands on a new
    `fingerprint` and therefore a new cache entry anyway. It stays because a cache
    with no way to clear it is a trap, and because the tests reset state through
    it rather than reaching into `_TOOLS`.
    """
    if servers is None:
        _TOOLS.clear()
        _INSTRUCTIONS.clear()
        return

    _TOOLS.pop(fingerprint(servers), None)
    _INSTRUCTIONS.pop(fingerprint(servers), None)


async def resolve_server(assistant_id: str, server_id: str) -> McpServer | None:
    """The configured server an id names, read off the assistant that owns it.

    The id, not a URL. A proxy that forwards wherever the caller points it is a
    general-purpose fetcher wearing the deployment's network position, and the
    browser gains nothing from naming the host: it is choosing between servers
    the assistant already has. Resolving server-side also keeps the bearer
    token out of the request entirely.

    `None` for an id the assistant does not have, which the caller answers 404.
    An unsaved server being edited in Settings is exactly that case, and is why
    "Test connection" cannot go through here.
    """
    # Local: langgraph_sdk pulls an http stack this module does not otherwise
    # need, and mcp_servers.py is on the graph's import path.
    from langgraph_sdk import get_client  # noqa: PLC0415

    if not assistant_id:
        raise LookupError("no assistant was named, so no server list can be read")

    try:
        assistant = await get_client().assistants.get(assistant_id)
    except Exception as exc:
        # The likeliest cause by far, and the one worth naming: the SPA falls
        # back to the GRAPH id when no assistant has been chosen, and a graph
        # id is not an assistant id.
        raise LookupError(
            f"could not read assistant {assistant_id!r}: {type(exc).__name__}: {exc}"
        ) from exc

    # `getattr`, not dot access: the SDK types an Assistant as a union of
    # TypedDict / dataclass / model shapes, so the checker rejects both
    # subscripting and `.get`, and only the mapping form exists at runtime.
    context: Any = getattr(assistant, "get", lambda _k, _d=None: None)("context") or {}
    servers_raw = context.get("mcp_servers") if hasattr(context, "get") else None
    configured = parse_servers(servers_raw)
    return next((s for s in configured if s.id == server_id), None)


def _log(message: str) -> None:
    """One-line diagnostic on the deployment's stdout."""
    print(f"[mcp] {message}", flush=True)
