"""Serve the MCP Apps: HTML fragments plus the shared bridge, chrome and styles.

An MCP App is HTML the server hands to the host, which renders it in a sandboxed
iframe once the tool call returns. Four of them here share one wire protocol and
one visual language, so `bridge.js` and `shell.css` live once and are composed
into each app at serve time, along with the window frame below. An app file is
therefore just its own markup, styles and logic, which is what makes a new one
cheap to add.

The iframe has no origin (`sandbox="allow-scripts"`, deliberately no
`allow-same-origin`), so nothing can be fetched: everything has to be inlined
into the document, which is exactly what this does.
"""

from __future__ import annotations

from functools import cache
from html import escape
from pathlib import Path

APPS = Path(__file__).parent / "apps"


@cache
def _shared() -> tuple[str, str]:
    """The bridge script and the shell stylesheet, read once."""
    return (
        (APPS / "bridge.js").read_text(encoding="utf-8"),
        (APPS / "shell.css").read_text(encoding="utf-8"),
    )


def _window(title: str, body: str) -> str:
    """The Windows 95 frame every app is served inside.

    Here rather than in each app file so a new app is markup and logic only, and
    so the chrome cannot drift between the four.

    The minimize, maximize and close boxes are DECORATIVE, and `aria-hidden` so a
    screen reader is not told about three controls that do nothing. SEP-1865 has
    no message for a view asking to be dismissed, and inventing one would give up
    the thing this server is demonstrating. Chrome that plainly does not respond
    beats a box that looks pressable and is not.
    """
    return (
        '<div class="win">\n'
        '  <div class="titlebar">\n'
        f'    <span class="title">{escape(title)}</span>\n'
        '    <span class="tbtns" aria-hidden="true">'
        '<span class="tbtn">_</span><span class="tbtn">&#9723;</span>'
        '<span class="tbtn">&#10005;</span></span>\n'
        "  </div>\n"
        f'  <div class="client">\n{body}\n  </div>\n'
        "</div>"
    )


def render_app(name: str, *, title: str) -> str:
    """One complete, self-contained MCP App document.

    Args:
        name: File stem under `apps/`, e.g. `"signature"`.
        title: Document title. Shown by a host that surfaces one, and drawn in
            the app's own title bar.

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
        f"<title>{escape(title)}</title>\n"
        f"<style>\n{css}\n</style>\n"
        f"<script>\n{bridge}\n</script>\n"
        "</head>\n<body>\n"
        f"{_window(title, body)}\n"
        "</body>\n</html>\n"
    )
