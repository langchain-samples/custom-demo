"""Shared plumbing for the provisioning modules.

`setup.py`, `evals.py` and `traffic.py` all import downward from here and nothing
in this module imports back. Keep it that way. A helper that any two of the three
need belongs here: define it in `setup.py` and import it from the other two and
there is an import cycle, which only function-local imports can hold apart.
"""

from __future__ import annotations

import re
from typing import Any

from custom_demo.config import scoped_client


def slugify(name: str) -> str:
    """Lowercase, hyphenate to a URL-safe slug (falls back to "customer")."""
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") or "customer"


def playground_model_id(client: Any, flags: tuple[str, ...]) -> str:
    """A workspace model carrying every flag in `flags`, or "" if there is none.

    `GET /playground-settings` lists the workspace's *model settings* — the records the
    UI's model pickers offer. Each carries availability flags per feature
    (`available_in_evaluators`, `available_in_insights_heavy`, ...), and every LangSmith
    feature that runs an LLM for you takes one of these ids rather than an API key. That
    is what lets a customer workspace with no model secret of its own still run an
    Insights job or an LLM-as-judge: the records backed by LangSmith's own LLM gateway
    (`LC_GATEWAY_KEY`) bill through LangSmith and need no customer credentials.

    Gateway-backed models are preferred for exactly that reason — anything else needs a
    key this workspace may not have, which is the failure being avoided.
    """
    settings = client.request_with_retries("GET", "/playground-settings").json()
    usable = [
        s
        for s in (settings if isinstance(settings, list) else [])
        if isinstance(s, dict) and s.get("id") and all(s.get(flag) for flag in flags)
    ]
    if not usable:
        return ""

    return str(sorted(usable, key=lambda s: 0 if "LC_GATEWAY_KEY" in str(s) else 1)[0]["id"])


def _ws_client(workspace: str | None):
    """Client for a target workspace. Kept as a name because two modules import it."""
    return scoped_client(workspace)
