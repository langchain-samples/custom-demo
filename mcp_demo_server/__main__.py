"""Serve the demo MCP server over stateless streamable HTTP.

    python -m mcp_demo_server      -> Meridian Wealth on http://127.0.0.1:8765/mcp

One server, one namespace. Our own adapter already prefixes every tool with the
server it came from, so mounting a second FastMCP under this one would produce
`meridian_meridian_propose_rebalance`.

`stateless_http=True` is the point of the exercise: no session is created, so
every request stands alone and a tunnel that reconnects (or a server that
restarts) does not strand an in-flight conversation.
"""

from __future__ import annotations

import os

HOST = os.getenv("MERIDIAN_HOST", "127.0.0.1")
PORT = int(os.getenv("MERIDIAN_PORT", "8765"))
PATH = os.getenv("MERIDIAN_PATH", "/mcp")


def main() -> None:
    """Run the demo server until interrupted."""
    from mcp_demo_server.server import mcp

    print(f"{mcp.name} (stateless HTTP) -> http://{HOST}:{PORT}{PATH}")
    mcp.run(
        transport="http",
        host=HOST,
        port=PORT,
        path=PATH,
        stateless_http=True,
        # A tunnel presents its own hostname, so the forwarded Host header will
        # not be one this server could have guessed. The tunnel is the boundary.
        allowed_hosts=["*"],
        allowed_origins=["*"],
    )


if __name__ == "__main__":
    main()
