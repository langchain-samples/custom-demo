"""Serve the MCP Apps: one React bundle, the window chrome and the shared styles.

An MCP App is HTML the server hands to the host, which renders it in a sandboxed
iframe once the tool call returns. The four here are React components under
`apps/src/`, compiled together by `apps/build.sh` into the single `apps/app.js`.
A served document is that bundle, `shell.css`, the window frame below, and a
mount node naming which of the four components to draw, so adding an app is a
component plus a line in the bundle's entry point.

One bundle for four apps because React is most of the weight and four bundles
would commit four copies of it. `apps/src/index.tsx` imports all four and reads
the name off the mount node.

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
    """The app bundle and the shell stylesheet, read once."""
    return (
        (APPS / "app.js").read_text(encoding="utf-8"),
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
        name: Which component the bundle mounts, e.g. `"signature"`. The
            accepted names are the keys of `APPS` in `apps/src/index.tsx`; a
            name that bundle does not know renders as a message saying so.
        title: Document title. Shown by a host that surfaces one, and drawn in
            the app's own title bar.

    Returns:
        A full HTML document with the shell stylesheet, the window chrome, the
            mount node and the React bundle all inlined.
    """
    bundle, css = _shared()
    mount = f'    <div id="root" data-app="{escape(name, quote=True)}"></div>'
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8" />\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1" />\n'
        f"<title>{escape(title)}</title>\n"
        f"<style>\n{css}\n</style>\n"
        "</head>\n<body>\n"
        f"{_window(title, mount)}\n"
        # Last, and inside the body, because the bundle's final statement looks
        # the mount node up by id. An inline script cannot be deferred, so a
        # `<script>` in the head would run against a document with no `#root`.
        f"<script>\n{bundle}\n</script>\n"
        "</body>\n</html>\n"
    )
