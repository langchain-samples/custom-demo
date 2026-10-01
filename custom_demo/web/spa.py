"""The built SPA, served by the deployment itself under `/ui`.

The image builds `frontend/` into `SPA_DIR` (see `frontend/scripts/build-in-image.sh`,
run by `dockerfile_lines` in langgraph.json). Unset, as under `langgraph dev`, these
routes are absent and the SPA runs from the Vite dev server instead.

The SPA learns where the API is and which token to send from `/ui/config.js`, written
per request, because neither is known when the image is built: every preview
deployment has its own URL, and `APP_SHARED_SECRET` is a runtime secret. That route
and the static files are exempt from the shared-token check in `custom_demo/auth.py`,
since a browser navigating to a page cannot attach a header.
"""

from __future__ import annotations

import json
import os

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import BaseRoute, Mount, Route
from starlette.staticfiles import StaticFiles

SPA_PREFIX = "/ui"


def is_spa_path(path: str) -> bool:
    """Whether `path` is the SPA itself rather than the API it calls."""
    return path in ("/", SPA_PREFIX) or path.startswith(f"{SPA_PREFIX}/")


async def spa_root(request: Request) -> Response:
    """Send a bare visit to the deployment's root to the SPA."""
    return RedirectResponse(f"{SPA_PREFIX}/")


async def spa_config(request: Request) -> Response:
    """Point the SPA at this origin and hand it the deployment token.

    The token reaches the same audience it always has: anyone who can load the SPA
    could already read it out of the bundle. auth.py says what it gates.
    """
    lg = {"apiKey": os.getenv("APP_SHARED_SECRET", "").strip()}
    body = f"window.LG = Object.assign({json.dumps(lg)}, {{ url: window.location.origin }});\n"
    return Response(body, media_type="text/javascript", headers={"Cache-Control": "no-store"})


def spa_routes() -> list[BaseRoute]:
    """The SPA's routes, or none when this image was built without it.

    `StaticFiles` checks the directory exists, so an image that sets `SPA_DIR` but
    failed to build into it refuses to start rather than serving 404s.
    """
    directory = os.getenv("SPA_DIR", "").strip()
    if not directory:
        return []

    return [
        Route("/", spa_root, methods=["GET"]),
        # Ahead of the mount, so it shadows the dev stub `frontend/public/config.js`.
        Route(f"{SPA_PREFIX}/config.js", spa_config, methods=["GET"]),
        Mount(SPA_PREFIX, StaticFiles(directory=directory, html=True)),
    ]
