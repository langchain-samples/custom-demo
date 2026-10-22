"""Print a new `AGENT_JWT_PRIVATE_KEY` line for `.env`.

The LangSmith deployment signs the tokens the SPA uses on the Managed Deep Agents
deployment with this key (`custom_demo/agent_auth.py`). Put the printed line in the
`.env` both deployments deploy from: the LangSmith one signs with it, and the MDA one
needs it for the agent's own calls back to its deployment (remote-task wake-ups).

    uv run python scripts/agent_auth_key.py >> .env

Rotating it signs every open browser tab out of the agent until the page reloads.
"""

from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def main() -> None:
    """Generate a 2048-bit RSA key and print it newline-escaped on one line."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    escaped = pem.strip().replace("\n", "\\n")
    print(f'AGENT_JWT_PRIVATE_KEY="{escaped}"')


if __name__ == "__main__":
    main()
