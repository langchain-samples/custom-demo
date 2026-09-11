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


async def load_tools(
    servers: tuple[McpServer, ...], *, refresh: bool = False, include_app_only: bool = False
) -> list[Any]:
    """Adapted LangChain tools for `servers`, cached for `TOOLS_TTL_SECONDS`.

    App-only tools are dropped unless `include_app_only`, because the agent must
    not see them (see `model_visible`). The cache holds every tool the server
    published and the filter runs on the way out, so the one caller that needs
    them, `call_app_tool` proxying for an app, does not force a second discovery.

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
            return hit[1] if include_app_only else [t for t in hit[1] if model_visible(t)]

    lock = _LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        # A second caller that queued on the lock while the first was loading
        # should use what it produced, not load again.
        hit = _TOOLS.get(key)
        if not refresh and hit and hit[0] > time.monotonic():
            return hit[1] if include_app_only else [t for t in hit[1] if model_visible(t)]

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
        return tools if include_app_only else [t for t in tools if model_visible(t)]


async def _discover(servers: tuple[McpServer, ...], *, refresh: bool) -> list[Any]:
    """One real discovery pass: connect, list, adapt. Every tool, unfiltered."""
    # Local like `build_group`'s: keeps the mcp client stack off graph load.
    from langchain.mcp import MCPAdapter  # noqa: PLC0415

    async with MCPAdapter(build_group(servers)) as adapter:
        # `use` reads the client-side cache when the server's TTL hint says it is
        # still fresh; `refresh` is what the SPA's "reload tools" button sends.
        return await adapter.list_tools(cache_mode="refresh" if refresh else "use")


def app_callable(tool: Any) -> bool:
    """Whether an MCP App on the same server may call this tool.

    Defaults to True, because an omitted `visibility` means `["model", "app"]`.
    """
    ui = _ui_meta(tool)
    visibility = ui.get("visibility")
    if not isinstance(visibility, list):
        return True

    return "app" in visibility


def _ui_meta(tool: Any) -> dict[str, Any]:
    """The `_meta.ui` block the adapter carried through, or an empty one."""
    meta = (tool.metadata or {}).get("mcp") or {}
    return (((meta.get("tool") or {}).get("_meta") or {}).get("ui")) or {}


def model_visible(tool: Any) -> bool:
    """Whether the agent is allowed to see this tool.

    MCP Apps (SEP-1865) lets a server mark a tool `_meta.ui.visibility: ["app"]`,
    meaning only its own App may call it, and the rule for a host is a MUST: a
    tool whose visibility omits `"model"` is kept out of the agent's tool list.
    Excalidraw's server is the worked example, publishing `create_view` to the
    model and `save_checkpoint` / `read_checkpoint` / `export_to_excalidraw` to
    the app alone.

    Defaults to visible. Omitting the key means `["model", "app"]`, which is
    every ordinary tool on every server that has never heard of the extension.
    """
    visibility = _ui_meta(tool).get("visibility")
    if not isinstance(visibility, list):
        return True

    return "model" in visibility


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

    MCP Apps (SEP-1865) stamps `_meta.ui.resourceUri` on the tool, and the adapter
    carries the tool's MCP provenance through on `metadata["mcp"]`.

    The flat `_meta["ui/resourceUri"]` is the extension's earlier spelling. The
    spec deprecates it but keeps it until GA, so a server built against a 2025
    SDK still sends it, and reading only the nested one would render that
    server's app as a generic form with no indication why.
    """
    meta = (tool.metadata or {}).get("mcp") or {}
    tool_meta = (meta.get("tool") or {}).get("_meta") or {}
    ui = tool_meta.get("ui") or {}
    uri = ui.get("resourceUri") or tool_meta.get("ui/resourceUri")
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

    result = await _read_uri(servers, tool_name, uri)
    for item in result:
        text = getattr(item, "text", None)
        if isinstance(text, str) and text.strip():
            return {
                "tool_name": tool_name,
                "resource_uri": uri,
                "mime_type": getattr(item, "mime_type", None) or "text/html",
                "html": text,
                # The tool's own JSON Schema, carried through for the host to put
                # in `hostContext.toolInfo.tool`. NOT optional: `Tool` requires
                # `inputSchema`, and the official app SDK validates the
                # initialize result, so a host that omits it is rejected by every
                # app built on that SDK (Excalidraw's says
                # `path: ["hostContext","toolInfo","tool","inputSchema"]`).
                # The adapter keeps the server's schema verbatim on `args_schema`.
                "input_schema": tool.args_schema
                if isinstance(tool.args_schema, dict)
                else {"type": "object"},
            }

    # A `ui://` URI that resolves to nothing readable is a server bug, and the
    # caller renders the generic form. Say so rather than returning a blank page.
    _log(f"{tool_name} declares {uri} but the resource carried no text")
    return None


async def call_app_tool(
    servers: tuple[McpServer, ...], app_tool: str, target: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Call `target` on behalf of the MCP App bound to `app_tool`.

    How a result-bound app submits: the server publishes a tool marked
    `visibility: ["app"]`, invisible to the model, and its app calls it. SEP-1865
    has the host proxy that, which is what this is.

    Two limits, both from the spec, and both enforced here rather than trusted to
    the app. The target must live on the SAME server the app's own tool came from,
    which resolving through the group gives us. And it must include `"app"` in its
    visibility: a host MUST reject an app calling a tool the server did not open
    to apps, or server-authored HTML could drive any tool the connection can
    reach.

    Raises on refusal. The app is holding a promise open, so it needs the reason.
    """
    tools = await load_tools(servers, include_app_only=True)
    by_name = {t.name: t for t in tools}
    if app_tool not in by_name:
        raise LookupError(f"{app_tool} is not a tool on any connected server")

    wanted = by_name.get(target)
    if wanted is None:
        raise LookupError(f"{target} is not a tool on any connected server")

    if not app_callable(wanted):
        raise PermissionError(
            f"{target} is not open to apps: its visibility does not include 'app'"
        )

    group = build_group(servers)
    async with group:
        # `ToolRoute` carries the server and the tool's own upstream name, which
        # is what the prefix is hiding. Comparing `server_name` is how the
        # same-server rule is enforced without parsing namespaced strings.
        opener = await group.resolve_tool(app_tool)
        route = await group.resolve_tool(target)
        if opener.server_name != route.server_name:
            raise PermissionError(
                f"{app_tool} may not call {target}: they are on different servers"
            )

        result = await route.client.call_tool(route.upstream_name, arguments)

    return {
        "structuredContent": getattr(result, "structured_content", None),
        "isError": bool(getattr(result, "is_error", False)),
    }


async def _read_uri(servers: tuple[McpServer, ...], tool_name: str, uri: str) -> list[Any]:
    """Read one resource from the server `tool_name` came from.

    Resolving through the group rather than picking a member is what binds the
    read to that one server: an app may read its own server's resources and
    nothing else, which is the boundary SEP-1865 draws for `resources/read`.
    """
    group = build_group(servers)
    async with group:
        route = await group.resolve_tool(tool_name)
        return await route.client.read_resource(uri)


async def read_app_resource(
    servers: tuple[McpServer, ...], tool_name: str, uri: str
) -> list[dict[str, Any]]:
    """Contents of `uri`, for an MCP App that asked the host to read it.

    An app runs in an origin-less iframe and cannot fetch anything itself, so
    `resources/read` is the only way it can reach its own server's data. The
    reference app uses it to load notes; ours do not need it yet, but a host that
    refuses it cannot render a third-party app that does.

    Returns one entry per content block, text or base64 blob, in the JSON shape
    `resources/read` puts on the wire. Raises rather than returning empty if the
    read fails: the app is waiting on a JSON-RPC response and a silent empty list
    would render as an app with no data and no reason why.
    """
    out: list[dict[str, Any]] = []
    for item in await _read_uri(servers, tool_name, uri):
        entry: dict[str, Any] = {
            "uri": str(getattr(item, "uri", uri)),
            "mimeType": getattr(item, "mime_type", None),
        }
        text = getattr(item, "text", None)
        blob = getattr(item, "blob", None)
        if isinstance(text, str):
            entry["text"] = text
        elif blob is not None:
            entry["blob"] = blob if isinstance(blob, str) else str(blob)

        out.append(entry)

    return out


def _log(message: str) -> None:
    """One-line diagnostic on the deployment's stdout."""
    print(f"[mcp] {message}", flush=True)
