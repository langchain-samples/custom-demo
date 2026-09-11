"""Remote MCP servers (`/mcp/probe`, `/mcp/app`, `/mcp/resource`, `/mcp/call`).

Four questions the SPA cannot answer itself, because a browser cannot speak
MCP: does this connection string work, does a tool ship its own UI, what is
behind a URI that app asked the host to read, and will we make a call on its
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
    probe,
    read_app,
    read_app_resource,
)


def _mcp_servers_from(payload: dict):
    """Parse the `servers` array out of a request body."""
    return parse_servers(payload.get("servers"))


async def mcp_probe(request):
    """Connect to each MCP server and report the tools it offers.

    Powers "Test connection" in Settings. Always 200: a server that refuses is a
    per-server `ok: false` with the reason, not a failed request, because the SPA
    renders one row per server and one dead tunnel must not blank the others.
    """
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - a malformed body is a client error, not a crash
        return JSONResponse({"error": "expected a JSON body"}, status_code=400)

    servers = _mcp_servers_from(payload)
    if not servers:
        return JSONResponse({"servers": []})

    return JSONResponse(await probe(servers))


async def mcp_app(request):
    """The HTML of the MCP App bound to `tool_name`, for the SPA to render.

    Called while a run is paused on that tool's elicitation. `app: null` is the
    ordinary answer for a tool with no UI, and the SPA falls back to the generic
    schema-driven form, so this is not an error path.
    """
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - a malformed body is a client error, not a crash
        return JSONResponse({"error": "expected a JSON body"}, status_code=400)

    tool_name = str(payload.get("tool_name") or "").strip()
    servers = _mcp_servers_from(payload)
    if not tool_name or not servers:
        return JSONResponse({"app": None})

    try:
        return JSONResponse({"app": await read_app(servers, tool_name)})
    except Exception as exc:  # noqa: BLE001 - fall back to the generic form, don't strand the pause
        return JSONResponse({"app": None, "error": f"{type(exc).__name__}: {exc}"[:300]})


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
