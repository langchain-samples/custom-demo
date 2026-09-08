"""Remote MCP servers (`POST /mcp/probe`, `POST /mcp/app`).

Two questions the SPA cannot answer itself, because a browser cannot speak MCP:
does this connection string work, and does the tool a run is paused on ship its
own UI. Both take the server list in the body rather than reading it off the
assistant, matching how /sandbox-files takes its keys: the SPA is the thing that
holds the draft config, and Settings has to validate a URL before it is saved.
"""

from __future__ import annotations

from starlette.responses import JSONResponse

from dashboard_agent.runtime.mcp_servers import parse_servers, probe, read_app


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
