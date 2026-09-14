"""Remote MCP servers (`/mcp/bootstrap`, `/mcp/resource`, `/mcp/call`).

Three questions the SPA cannot answer itself, because a browser cannot speak
MCP: what do these servers offer (and which of their tools ship a UI), what is
behind a URI an app asked the host to read, and will we make a call on its
behalf. All take the
server list in the body rather than reading it off the assistant, matching how
/sandbox-files takes its keys: the SPA is the thing that holds the draft config,
and Settings has to validate a URL before it is saved.
"""

from __future__ import annotations

from starlette.responses import JSONResponse

from custom_demo.runtime.mcp_servers import (
    call_app_tool,
    parse_servers,
    read_app_resource,
    tool_catalog,
)


def _mcp_servers_from(payload: dict):
    """Parse the `servers` array out of a request body."""
    return parse_servers(payload.get("servers"))


async def mcp_bootstrap(request):
    """Every configured server and the tools it offers, for the SPA.

    Two things at once, both impossible in the browser: which tools ship a UI
    (`_meta.ui.resourceUri` is only on `tools/list`), and what each server
    offers, so the Settings list renders on page load instead of after a click.

    Always 200 with a `servers` array. Empty is the ordinary answer for no
    configured servers, and a server whose tools are unknown comes back
    `ok: false` beside the others rather than failing the request, so one dead
    tunnel never blanks the rest.

    `refresh` in the body reconnects to every server and reports each failure's
    reason: that is "Test connection", and the only authoritative answer about
    whether a tunnel is up. Without it, `ok` means no more than "we know this
    server's tools".

    Plain JSON, not SSE. Claude's equivalent streams because it fans out over
    many connectors of which most may be cold or broken, and a page-load
    critical path must not wait for the slowest. Ours answers off a warm
    `load_tools` cache for one or two servers, so there is nothing to stream.
    Revisit that if a demo ever carries enough servers for the tail to show.
    """
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - a malformed body is a client error, not a crash
        return JSONResponse({"error": "expected a JSON body"}, status_code=400)

    servers = _mcp_servers_from(payload)
    if not servers:
        return JSONResponse({"servers": []})

    # `refresh` is what "Test connection" sends. Without it this is served off
    # the warm cache, which is the page-load path and must stay cheap.
    refresh = bool(payload.get("refresh"))
    try:
        return JSONResponse({"servers": await tool_catalog(servers, refresh=refresh)})
    except Exception as exc:  # noqa: BLE001 - a chat must still run when discovery fails
        return JSONResponse({"servers": [], "error": f"{type(exc).__name__}: {exc}"[:300]})


async def mcp_resource(request):
    """Read a resource on behalf of an MCP App (`resources/read` from a View).

    The app is running in an origin-less iframe, so it cannot fetch: SEP-1865
    has it ask the host, and the host proxy the read to the server the app's tool
    came from. `tool_name` is what binds the read to that one server.

    Errors are returned, not swallowed. Unlike /mcp/app there is no fallback
    rendering here: the app is blocked on a JSON-RPC response, so it needs either
    contents or a reason it cannot have them.
    """
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - a malformed body is a client error, not a crash
        return JSONResponse({"error": "expected a JSON body"}, status_code=400)

    tool_name = str(payload.get("tool_name") or "").strip()
    uri = str(payload.get("uri") or "").strip()
    servers = _mcp_servers_from(payload)
    if not tool_name or not uri or not servers:
        return JSONResponse(
            {"error": "tool_name, uri and servers are all required"}, status_code=400
        )

    try:
        return JSONResponse({"contents": await read_app_resource(servers, tool_name, uri)})
    except Exception as exc:  # noqa: BLE001 - the app needs the reason, not a blank render
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"[:300]}, status_code=502)


async def mcp_call(request):
    """Make a tool call on behalf of an MCP App (`tools/call` from a View).

    How a result-bound app submits what a person did: the server publishes a
    tool marked `visibility: ["app"]` and its app calls it. SEP-1865 has the host
    proxy that, and `call_app_tool` is where the two rules live, both refusals
    rather than filters: same server as the app's own tool, and the target must
    be open to apps.

    A refusal is a 403 with the reason, because the app is blocked on a JSON-RPC
    response and a silent failure looks to the person like a button that does
    nothing.
    """
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - a malformed body is a client error, not a crash
        return JSONResponse({"error": "expected a JSON body"}, status_code=400)

    app_tool = str(payload.get("app_tool") or "").strip()
    target = str(payload.get("name") or "").strip()
    arguments = payload.get("arguments")
    servers = _mcp_servers_from(payload)
    if not app_tool or not target or not servers:
        return JSONResponse(
            {"error": "app_tool, name and servers are all required"}, status_code=400
        )

    if not isinstance(arguments, dict):
        arguments = {}

    try:
        return JSONResponse(await call_app_tool(servers, app_tool, target, arguments))
    except (PermissionError, LookupError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)
    except Exception as exc:  # noqa: BLE001 - the app needs the reason, not a dead button
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"[:300]}, status_code=502)
