"""The issuer side of `custom_demo/agent_auth.py`: the JWKS and a token for the SPA.

`GET /agent-auth/jwks.json` is public, because MDA fetches it with no credentials and a
public key is not a secret; `custom_demo/auth.py` exempts it. `POST /agent-auth/token` is
an ordinary custom route behind the deployment's shared-token auth, so only a caller that
already holds the app password gets a token. The SPA calls it when its token nears expiry.
"""

from __future__ import annotations

from starlette.responses import JSONResponse

from custom_demo.agent_auth import (
    AgentAuthNotConfiguredError,
    agent_url,
    jwks,
    mint_token,
)
from custom_demo.web.errors import err


async def agent_jwks(request):
    """The public key MDA verifies the SPA's tokens with."""
    try:
        body = jwks()
    except AgentAuthNotConfiguredError as exc:
        return err(404, "agent_auth_not_configured", str(exc))

    return JSONResponse(body, headers={"Cache-Control": "public, max-age=300"})


async def agent_token(request):
    """A fresh token for the MDA deployment, with its URL and expiry."""
    if not agent_url():
        return err(404, "agent_not_on_mda", "AGENT_URL is not set: the agent runs here.")

    try:
        token, expires = mint_token()
    except AgentAuthNotConfiguredError as exc:
        return err(500, "agent_auth_not_configured", str(exc))

    return JSONResponse({"url": agent_url(), "token": token, "expires_at": expires})
