"""Serve the Fieldlink MCP server over stateless streamable HTTP.

`python -m mcp_demo_server` → http://127.0.0.1:8765/mcp

`stateless_http=True` is the point of the exercise: no session is created, so
every request stands alone and a tunnel that reconnects (or a server that
restarts) does not strand an in-flight conversation.
"""

from __future__ import annotations

import os

from mcp_demo_server.server import mcp

HOST = os.getenv("FIELDLINK_HOST", "127.0.0.1")
PORT = int(os.getenv("FIELDLINK_PORT", "8765"))
PATH = os.getenv("FIELDLINK_PATH", "/mcp")


def main() -> None:
    """Run the server until interrupted."""
    print(f"Fieldlink MCP (stateless HTTP) -> http://{HOST}:{PORT}{PATH}")
    mcp.run(
        transport="http",
        host=HOST,
        port=PORT,
        path=PATH,
        stateless_http=True,
        # ngrok presents its own hostname, so the tunnelled Host header will not
        # be one this server could have guessed. The tunnel is the trust boundary.
        allowed_hosts=["*"],
        allowed_origins=["*"],
    )


if __name__ == "__main__":
    main()
