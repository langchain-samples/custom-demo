"""Serve the SPA (dashboard_agent/static) with no-cache headers.

`python -m http.server` sends no cache headers, so browsers cache index.html and
app.js and a plain reload keeps stale code. This server sends `no-store` so every
request gets the latest files — no cache-busting query params or hard refresh
needed during development.

Usage: python scripts/serve_spa.py [port]   (default 3000)
"""

from __future__ import annotations

import contextlib
import http.server
import os
import sys

STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "dashboard_agent", "static")


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        super().end_headers()


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), NoCacheHandler)
    print(f"SPA (no-cache) → http://127.0.0.1:{port}")
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()


if __name__ == "__main__":
    main()
