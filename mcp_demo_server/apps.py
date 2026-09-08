"""Serve the MCP Apps: HTML fragments plus the shared bridge and stylesheet.

An MCP App is HTML the server hands to the host, which renders it in a sandboxed
iframe while a tool call is paused. Four of them here share one postMessage
protocol and one visual language, so `bridge.js` and `shell.css` live once and
are composed into each app at serve time. An app file is therefore just its own
markup, styles and logic, which is what makes a new one cheap to add.

The iframe has no origin (`sandbox="allow-scripts"`, deliberately no
`allow-same-origin`), so nothing can be fetched: everything has to be inlined
into the document, which is exactly what this does.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

APPS = Path(__file__).parent / "apps"


@cache
def _shared() -> tuple[str, str]:
    """The bridge script and the shell stylesheet, read once."""
    return (
        (APPS / "bridge.js").read_text(encoding="utf-8"),
        (APPS / "shell.css").read_text(encoding="utf-8"),
    )


def render_app(name: str, *, title: str) -> str:
    """One complete, self-contained MCP App document.

    Args:
        name: File stem under `apps/`, e.g. `"signature"`.
        title: Document title, shown by a host that surfaces one.

    Returns:
        A full HTML document with the shell stylesheet and bridge inlined ahead
            of the app's own markup, so the app can call `McpApp` immediately.
    """
    bridge, css = _shared()
    body = (APPS / f"{name}.html").read_text(encoding="utf-8")
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8" />\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1" />\n'
        f"<title>{title}</title>\n"
        f"<style>\n{css}\n</style>\n"
        f"<script>\n{bridge}\n</script>\n"
        "</head>\n<body>\n"
        f"{body}\n"
        "</body>\n</html>\n"
    )
