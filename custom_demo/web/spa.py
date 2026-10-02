"""The built SPA, served by the deployment itself under `/ui`.

The image builds `frontend/` into `SPA_DIR` (see `frontend/scripts/build-in-image.sh`,
run by `dockerfile_lines` in langgraph.json). Unset, as under `langgraph dev`, these
routes are absent and the SPA runs from the Vite dev server instead.

The SPA learns where the API is and which token to send from `/ui/config.js`, written
per request, because neither is known when the image is built: every preview
deployment has its own URL, and `APP_SHARED_SECRET` is a runtime secret.

A browser navigating to a page cannot attach `x-api-key`, so these paths are exempt
from the check in `custom_demo/auth.py` and gated here instead: `/ui/login` asks for
that same secret and, when it matches, sets a session cookie every later `/ui`
request carries, `config.js` included. So the token only reaches someone who typed it.
The cookie's value is an HMAC of the secret, so rotating the secret signs everyone out.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from html import escape
from urllib.parse import parse_qs

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
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


SESSION_COOKIE = "custom_demo_session"
SESSION_DAYS = 30
LOGIN_PATH = "/login"


def _secret() -> str:
    return os.getenv("APP_SHARED_SECRET", "").strip()


def session_value(secret: str) -> str:
    """The cookie value that proves its holder typed `secret`."""
    return hmac.new(secret.encode(), b"custom-demo-ui-session", hashlib.sha256).hexdigest()


def session_ok(cookie: str | None, secret: str) -> bool:
    """Whether a session cookie was issued for the current secret. Open without one."""
    if not secret:
        return True

    return hmac.compare_digest((cookie or "").encode(), session_value(secret).encode())


_LOGIN_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin: 0; min-height: 100vh; display: grid; place-items: center;
         background: #0b0d10; color: #e6e8eb; font: 15px/1.5 system-ui, sans-serif; }}
  form {{ width: min(340px, calc(100vw - 32px)); display: grid; gap: 12px; padding: 24px;
         border: 1px solid #23272e; border-radius: 12px; background: #12151a; }}
  h1 {{ margin: 0 0 4px; font-size: 17px; font-weight: 600; }}
  input {{ padding: 10px 12px; border-radius: 8px; border: 1px solid #2c313a;
          background: #0b0d10; color: inherit; font: inherit; }}
  button {{ padding: 10px 12px; border: 0; border-radius: 8px; background: #3b82f6;
           color: white; font: inherit; font-weight: 600; cursor: pointer; }}
  .error {{ margin: 0; color: #f87171; font-size: 13px; }}
</style>
</head>
<body>
<form method="post" action="{action}">
  <h1>Sign in</h1>
  <input type="password" name="password" placeholder="Password" autocomplete="current-password" autofocus required>
  {error}
  <button type="submit">Continue</button>
</form>
</body>
</html>
"""


def _login_page(action: str, failed: bool) -> HTMLResponse:
    error = '<p class="error">That password is not right.</p>' if failed else ""
    return HTMLResponse(
        _LOGIN_PAGE.format(action=escape(action), error=error),
        status_code=401 if failed else 200,
        headers={"Cache-Control": "no-store"},
    )


async def spa_login(request: Request) -> Response:
    """Ask for the shared secret; on a match, set the session cookie and enter the SPA."""
    action = f"{SPA_PREFIX}{LOGIN_PATH}"
    if request.method == "GET":
        return _login_page(action, failed=False)

    # Parsed by hand: `request.form()` needs python-multipart, which this package does
    # not depend on directly.
    fields = parse_qs((await request.body()).decode(errors="replace"))
    password = (fields.get("password") or [""])[0].strip()
    secret = _secret()
    if not hmac.compare_digest(password.encode(), secret.encode()):
        return _login_page(action, failed=True)

    response = RedirectResponse(f"{SPA_PREFIX}/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        session_value(secret),
        max_age=SESSION_DAYS * 86400,
        path=SPA_PREFIX,
        httponly=True,
        # The deployment sits behind a TLS-terminating proxy, so the scheme this app
        # sees is not the browser's; the forwarded one is.
        secure=request.headers.get("x-forwarded-proto", request.url.scheme) == "https",
        samesite="lax",
    )
    return response


class RequireSession:
    """Send a request without a valid session cookie to the login page."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap `app`."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Pass signed-in HTTP requests and the login page through; redirect the rest."""
        login = scope["type"] == "http" and scope["path"] == f"{SPA_PREFIX}{LOGIN_PATH}"
        if scope["type"] != "http" or login:
            await self.app(scope, receive, send)
            return

        cookies = Request(scope).cookies
        if session_ok(cookies.get(SESSION_COOKIE), _secret()):
            await self.app(scope, receive, send)
            return

        await RedirectResponse(f"{SPA_PREFIX}{LOGIN_PATH}", status_code=303)(scope, receive, send)


async def spa_config(request: Request) -> Response:
    """Point the SPA at this origin and hand it the deployment token.

    Reached only through `RequireSession`, so it returns the token to someone who
    signed in with it.
    """
    lg = {"apiKey": _secret()}
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
            Route(LOGIN_PATH, spa_login, methods=["GET", "POST"]),
            Mount("/", StaticFiles(directory=directory, html=True)),
        ]
    )
    return [
        Route("/", spa_root, methods=["GET"]),
        Mount(SPA_PREFIX, RequireSession(spa)),
    ]
