"""Serve Contoso Fleet Ops over stateless streamable HTTP.

    python -m fleet_mcp_server      -> Contoso Fleet Ops on http://127.0.0.1:8766/mcp

Its own port, so it runs beside the Meridian server (8765) without touching it.
"""

from __future__ import annotations

import os

from fleet_mcp_server.server import mcp

HOST = os.getenv("FLEET_MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("FLEET_MCP_PORT", "8766"))
PATH = os.getenv("FLEET_MCP_PATH", "/mcp")


def main() -> None:
    """Run the fleet server until interrupted."""
    print(f"{mcp.name} (stateless HTTP) -> http://{HOST}:{PORT}{PATH}")
    mcp.run(
        transport="http",
        host=HOST,
        port=PORT,
        path=PATH,
        stateless_http=True,
        allowed_hosts=["*"],
        allowed_origins=["*"],
    )


if __name__ == "__main__":
    main()
