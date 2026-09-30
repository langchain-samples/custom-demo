"""Serve HPE Fleet Ops over stateless streamable HTTP.

    python -m mcp_demo_server.hpe      -> HPE Fleet Ops on http://127.0.0.1:8766/mcp

Its own port, so it runs beside the Meridian server (8765) without touching it.
"""

from __future__ import annotations

import os

from mcp_demo_server.hpe.server import mcp

HOST = os.getenv("HPE_MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("HPE_MCP_PORT", "8766"))
PATH = os.getenv("HPE_MCP_PATH", "/mcp")


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
