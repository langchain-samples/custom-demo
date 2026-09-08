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

# A server that has gone away must not hang a turn. Both the probe route and the
# per-run load are bounded by this.
CONNECT_TIMEOUT_SECONDS = mcp_timeout_seconds()

_SLUG = re.compile(r"[^a-z0-9]+")


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
    Settings must not take the whole agent down, and the SPA validates properly
    through `probe()`.

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

    return ClientGroup(
        {
            s.id: Client(
                StreamableHttpTransport(s.url, headers=s.headers or None),
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


async def load_tools(servers: tuple[McpServer, ...], *, refresh: bool = False) -> list[Any]:
    """Adapted LangChain tools for `servers`, cached for `TOOLS_TTL_SECONDS`.

    Never raises: a server that is down, tunnelled to nothing, or refusing the
    token yields no tools and leaves the rest of the agent working. The SPA's
    `probe()` is where a connection problem is meant to be visible; a chat turn
    is not.
    """
    if not servers:
        return []

    key = fingerprint(servers)
    now = time.monotonic()
    if not refresh:
        hit = _TOOLS.get(key)
        if hit and hit[0] > now:
            return hit[1]

    lock = _LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        # A second caller that queued on the lock while the first was loading
        # should use what it produced, not load again.
        hit = _TOOLS.get(key)
        if not refresh and hit and hit[0] > time.monotonic():
            return hit[1]

        try:
            tools = await asyncio.wait_for(
                _discover(servers, refresh=refresh),
                timeout=CONNECT_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 - a bad server degrades the turn, never fails it
            _log(
                f"tool discovery failed for {[s.id for s in servers]}: {type(exc).__name__}: {exc}"
            )
            # Cache the emptiness briefly too, so an unreachable server is not
            # retried (and re-timed-out) on every single model call in a turn.
            _TOOLS[key] = (time.monotonic() + min(TOOLS_TTL_SECONDS, 30.0), [])
            return []

        _TOOLS[key] = (time.monotonic() + TOOLS_TTL_SECONDS, tools)
        return tools


async def _discover(servers: tuple[McpServer, ...], *, refresh: bool) -> list[Any]:
    """One real discovery pass: connect, list, adapt."""
    # Local like `build_group`'s: keeps the mcp client stack off graph load.
    from langchain.mcp import MCPAdapter  # noqa: PLC0415

    async with MCPAdapter(build_group(servers)) as adapter:
        # `use` reads the client-side cache when the server's TTL hint says it is
        # still fresh; `refresh` is what the SPA's "reload tools" button sends.
        return await adapter.list_tools(cache_mode="refresh" if refresh else "use")


def invalidate(servers: tuple[McpServer, ...] | None = None) -> None:
    """Drop cached tools, for one server set or all of them."""
    if servers is None:
        _TOOLS.clear()
        return

    _TOOLS.pop(fingerprint(servers), None)


async def probe(servers: tuple[McpServer, ...]) -> dict[str, Any]:
    """Connect and report what each server offers, for the Settings panel.

    Reports per server so one dead tunnel does not read as "MCP is broken".
    """
    out: list[dict[str, Any]] = []
    for server in servers:
        single = (server,)
        entry: dict[str, Any] = {**server.redacted(), "ok": False, "tools": []}
        try:
            tools = await asyncio.wait_for(
                _discover(single, refresh=True), timeout=CONNECT_TIMEOUT_SECONDS
            )
            entry["ok"] = True
            entry["tools"] = [
                {
                    "name": t.name,
                    "description": (t.description or "").strip().split("\n")[0][:200],
                    "app": app_uri(t),
                }
                for t in tools
            ]
            # A successful probe is a fresh discovery; let the agent reuse it.
            _TOOLS[fingerprint(single)] = (time.monotonic() + TOOLS_TTL_SECONDS, tools)
        except TimeoutError:
            entry["error"] = f"no response within {CONNECT_TIMEOUT_SECONDS:.0f}s"
        except Exception as exc:  # noqa: BLE001 - the message is the whole point of a probe
            entry["error"] = f"{type(exc).__name__}: {exc}"[:300]

        out.append(entry)

    return {"servers": out}


def app_uri(tool: Any) -> str | None:
    """The `ui://` resource an MCP App tool renders, or None for an ordinary tool.

    The MCP Apps extension stamps `_meta.ui.resourceUri` on the tool, and the
    adapter carries the tool's MCP provenance through on `metadata["mcp"]`.
    """
    meta = (tool.metadata or {}).get("mcp") or {}
    ui = ((meta.get("tool") or {}).get("_meta") or {}).get("ui") or {}
    uri = ui.get("resourceUri")
    return str(uri) if isinstance(uri, str) and uri.startswith("ui://") else None


async def read_app(servers: tuple[McpServer, ...], tool_name: str) -> dict[str, Any] | None:
    """The HTML of the MCP App bound to `tool_name`, or None if it has none.

    Called while a run is paused on that tool's elicitation, so the SPA can render
    the server's own UI for the pause instead of a generic form. Reads the
    resource from the server the tool actually came from, which is why it resolves
    through the group rather than guessing at a member.
    """
    tools = await load_tools(servers)
    tool = next((t for t in tools if t.name == tool_name), None)
    if tool is None:
        return None

    uri = app_uri(tool)
    if uri is None:
        return None

    group = build_group(servers)
    async with group:
        route = await group.resolve_tool(tool_name)
        result = await route.client.read_resource(uri)

    for item in result:
        text = getattr(item, "text", None)
        if isinstance(text, str) and text.strip():
            return {
                "tool_name": tool_name,
                "resource_uri": uri,
                "mime_type": getattr(item, "mime_type", None) or "text/html",
                "html": text,
            }

    # A `ui://` URI that resolves to nothing readable is a server bug, and the
    # caller renders the generic form. Say so rather than returning a blank page.
    _log(f"{tool_name} declares {uri} but the resource carried no text")
    return None


def _log(message: str) -> None:
    """One-line diagnostic on the deployment's stdout."""
    print(f"[mcp] {message}", flush=True)
