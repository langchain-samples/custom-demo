"""Shared-secret auth for the deployed Agent Server.

Replaces LangSmith's default data-plane auth with a single app token. The token
only gates *calling this deployment* — it is NOT a LangSmith key and carries no
workspace/org powers, so it is safe(-ish) to ship in the SPA bundle (its blast
radius is "someone can invoke this demo agent").

Enforced only when APP_SHARED_SECRET is set, so `langgraph dev` stays open for
local development. The SPA sends the token as the `x-api-key` header (see
frontend getApiKey()); a `Authorization: Bearer <token>` header also works.
"""

from __future__ import annotations

import hmac
import os

from langgraph_sdk import Auth

auth = Auth()


def _provided_token(headers: dict[bytes, bytes] | None, authorization: str | None) -> str:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    raw = (headers or {}).get(b"x-api-key")
    if isinstance(raw, bytes):
        return raw.decode(errors="ignore")
    return raw or ""


@auth.authenticate
async def authenticate(
    headers: dict[bytes, bytes] | None = None,
    authorization: str | None = None,
) -> dict[str, str]:
    secret = os.getenv("APP_SHARED_SECRET", "").strip()
    if not secret:
        # No secret configured → auth disabled (local dev).
        return {"identity": "anonymous"}
    token = _provided_token(headers, authorization)
    if not hmac.compare_digest(token, secret):
        raise Auth.exceptions.HTTPException(status_code=401, detail="Invalid or missing app token")
    return {"identity": "demo"}
