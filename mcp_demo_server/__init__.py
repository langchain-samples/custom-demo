"""The local demo MCP server the assistant connects to over ngrok.

Not part of the deployment (the wheel packages only `dashboard_agent`); this is
the other end of the connection. See `server.py`.
"""

from mcp_demo_server.server import SIGNATURE_URI, mcp

__all__ = ["SIGNATURE_URI", "mcp"]
