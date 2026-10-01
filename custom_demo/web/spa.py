"""The built SPA, served by the deployment itself under `/ui`.

The image builds `frontend/` into `SPA_DIR` (see `frontend/scripts/build-in-image.sh`,
run by `dockerfile_lines` in langgraph.json). Unset, as under `langgraph dev`, these
routes are absent and the SPA runs from the Vite dev server instead.

The SPA learns where the API is and which token to send from `/ui/config.js`, written
per request, because neither is known when the image is built: every preview
deployment has its own URL, and `APP_SHARED_SECRET` is a runtime secret.

A browser navigating to a page cannot attach `x-api-key`, so these paths are exempt
from the check in `custom_demo/auth.py` and gated here instead, by HTTP Basic auth
whose password is that same secret. The browser prompts once and resends it on every
`/ui` request, `config.js` included, so the token only reaches someone who typed it.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import os

from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from starlette.routing import BaseRoute, Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

SPA_PREFIX = "/ui"


def is_spa_path(path: str) -> bool:
    """Whether `path` is the SPA itself rather than the API it calls."""
    return path in ("/", SPA_PREFIX) or path.startswith(f"{SPA_PREFIX}/")


async def spa_root(request: Request) -> Response:
    """Send a bare visit to the deployment's root to the SPA."""
    return RedirectResponse(f"{SPA_PREFIX}/")


def basic_auth_ok(authorization: str | None, secret: str) -> bool:
    """Whether a Basic `Authorization` header carries `secret` as its password.

    Any username is accepted: the secret is the whole credential, as in auth.py.
    """
    if not secret:
        return True

    scheme, _, encoded = (authorization or "").partition(" ")
    if scheme.lower() != "basic":
        return False

    try:
        decoded = base64.b64decode(encoded, validate=True).decode()
    except (binascii.Error, UnicodeDecodeError):
        return False

    _, _, password = decoded.partition(":")
    return hmac.compare_digest(password.encode(), secret.encode())


class RequireSecret:
    """Answer 401 with a Basic challenge unless the request carries the secret."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap `app`."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Pass authorized HTTP requests through; challenge the rest."""
        secret = os.getenv("APP_SHARED_SECRET", "").strip()
        if scope["type"] == "http" and not basic_auth_ok(
            Headers(scope=scope).get("authorization"), secret
        ):
            challenge = PlainTextResponse(
                "Enter the deployment's shared secret as the password.",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="custom-demo", charset="UTF-8"'},
            )
            await challenge(scope, receive, send)
            return

        await self.app(scope, receive, send)


async def spa_config(request: Request) -> Response:
    """Point the SPA at this origin and hand it the deployment token.

    Reached only through `RequireSecret`, so it returns the token to someone who
    just supplied it.
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

    spa = Starlette(
        routes=[
            # Ahead of the files, so it shadows the dev stub `frontend/public/config.js`.
            Route("/config.js", spa_config, methods=["GET"]),
            Mount("/", StaticFiles(directory=directory, html=True)),
        ]
    )
    return [
        Route("/", spa_root, methods=["GET"]),
        Mount(SPA_PREFIX, RequireSecret(spa)),
    ]
