"""Tokens the LangSmith deployment issues for calling the Managed Deep Agents deployment.

The agent runs on MDA, whose identity layer verifies JWTs against a JWKS URL and has
no notion of this app's shared password. So the LangSmith deployment, which already
gates the SPA behind that password, is the issuer: it signs a short-lived RS256 token
(`mint_token`) and publishes the public half (`jwks`) at `/agent-auth/jwks.json`, and
the root `identity.py` tells MDA to trust exactly that issuer and audience. The browser
then calls MDA directly with `Authorization: Bearer <token>`; nothing is proxied.

Every token names the same subject, `AGENT_JWT_SUBJECT` (default "demo"). MDA scopes
threads to the subject, so one shared subject keeps the app's existing behaviour: anyone
past the password sees the same threads, as they do on the LangSmith deployment today.

Unconfigured (no `AGENT_JWT_PRIVATE_KEY`) means the agent is not on MDA, and every
caller here raises `AgentAuthNotConfiguredError` rather than minting an unverifiable
token.
"""

from __future__ import annotations

import base64
import hashlib
import os
import time
from functools import cache

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

# What MDA's identity provider checks `aud` against. A constant rather than config: it
# only has to agree with `identity.py`, which imports it.
AUDIENCE = "custom-demos-agent"
JWKS_PATH = "/agent-auth/jwks.json"
TOKEN_TTL_SECONDS = 3600


class AgentAuthNotConfiguredError(RuntimeError):
    """A token was asked for, but no signing key or issuer is configured."""


def agent_url() -> str:
    """The MDA deployment's base URL, "" when the agent runs on this deployment."""
    return os.getenv("AGENT_URL", "").strip().rstrip("/")


def issuer() -> str:
    """The issuer URL: the LangSmith deployment's public origin, where the JWKS lives."""
    value = os.getenv("AGENT_JWT_ISSUER", "").strip().rstrip("/")
    if not value:
        raise AgentAuthNotConfiguredError(
            "AGENT_JWT_ISSUER is not set. Set it to this deployment's public URL, the same "
            "value on both the LangSmith and the MDA deployment."
        )

    return value


def subject() -> str:
    """The subject every token names (see the module docstring for why it is shared)."""
    return os.getenv("AGENT_JWT_SUBJECT", "").strip() or "demo"


@cache
def _private_key(pem: str) -> RSAPrivateKey:
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    if not isinstance(key, RSAPrivateKey):
        raise AgentAuthNotConfiguredError("AGENT_JWT_PRIVATE_KEY must be an RSA private key.")

    return key


def _signing_key() -> RSAPrivateKey:
    # `\n`-escaped is how a multi-line PEM survives a .env file and a deployment secret.
    pem = os.getenv("AGENT_JWT_PRIVATE_KEY", "").strip().replace("\\n", "\n")
    if not pem:
        raise AgentAuthNotConfiguredError(
            "AGENT_JWT_PRIVATE_KEY is not set, so no token for the MDA agent can be signed. "
            "Generate one with `uv run python scripts/agent_auth_key.py`."
        )

    return _private_key(pem)


def _b64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _key_id(key: RSAPrivateKey) -> str:
    der = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return hashlib.sha256(der).hexdigest()[:16]


def jwks() -> dict:
    """The public signing key as a JWKS document, for MDA to verify tokens against."""
    key = _signing_key()
    numbers = key.public_key().public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": _key_id(key),
                "n": _b64url_uint(numbers.n),
                "e": _b64url_uint(numbers.e),
            }
        ]
    }


def mint_token(ttl_seconds: int = TOKEN_TTL_SECONDS) -> tuple[str, int]:
    """A signed token for the MDA deployment and its expiry (unix seconds)."""
    key = _signing_key()
    now = int(time.time())
    expires = now + ttl_seconds
    claims = {
        "iss": issuer(),
        "aud": AUDIENCE,
        "sub": subject(),
        "iat": now,
        "exp": expires,
    }
    token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": _key_id(key)})
    return token, expires


def agent_headers() -> dict[str, str]:
    """Headers for a server-side call to the MDA deployment."""
    token, _expires = mint_token(ttl_seconds=300)
    return {"Authorization": f"Bearer {token}"}
