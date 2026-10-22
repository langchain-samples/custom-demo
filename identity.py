"""Managed Deep Agents identity: trust tokens the LangSmith deployment signs.

See `custom_demo/agent_auth.py`. The LangSmith deployment checks the app's shared
password, then signs a short-lived RS256 token; MDA verifies it against the JWKS that
deployment publishes. A generic validated-token provider, the same shape
`auth.supabase(...)` produces, pointed at our own issuer.

`AGENT_JWT_ISSUER` is read when the deployment imports this file, from the secrets
`mda deploy` forwards out of `.env`.
"""

from managed_deepagents import define_identity

from custom_demo.agent_auth import AUDIENCE, JWKS_PATH, issuer

identity = define_identity(
    auth={
        "id": "custom-demos",
        "issuer": issuer(),
        "jwks": f"{issuer()}{JWKS_PATH}",
        "audience": AUDIENCE,
        "algorithms": ["RS256"],
        "claims": {"user": "sub"},
    }
)
