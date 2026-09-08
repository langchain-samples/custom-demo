"""Serve one of the demo MCP servers over stateless streamable HTTP.

    python -m mcp_demo_server              -> Fieldlink Logistics  (default)
    python -m mcp_demo_server --wealth     -> Meridian Wealth

Two servers rather than one because the tools belong to different businesses,
and a logistics book with a portfolio rebalancer in it is not a demo anyone
believes. They are not mounted together either: our own adapter already prefixes
every tool with the server it came from, so a second namespace here would produce
`meridian_meridian_propose_rebalance`.

`stateless_http=True` is the point of the exercise: no session is created, so
every request stands alone and a tunnel that reconnects (or a server that
restarts) does not strand an in-flight conversation.
"""

from __future__ import annotations

import os
import sys

HOST = os.getenv("FIELDLINK_HOST", "127.0.0.1")
PORT = int(os.getenv("FIELDLINK_PORT", "8765"))
PATH = os.getenv("FIELDLINK_PATH", "/mcp")


def main() -> None:
    """Run the selected server until interrupted."""
    wealth = "--wealth" in sys.argv or os.getenv("FIELDLINK_DOMAIN") == "wealth"
    if wealth:
        from mcp_demo_server.wealth import mcp
    else:
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
