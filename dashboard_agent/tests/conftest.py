"""Shared test configuration.

Unit tests must never provision a real sandbox VM (slow, costs money, needs a live
LangSmith tenant). The code-execution backend is always-on in production but is
gated by `SANDBOX_ENABLED`; default it OFF for the whole suite so a developer's real
`LANGSMITH_API_KEY` can't make the deterministic tests spin up VMs. The sandbox
spec tests opt back in explicitly (they set `SANDBOX_ENABLED=1` with a fake client).

The NEW name, deliberately. `config.sandbox_enabled` resolves the new spelling before
the deprecated `DA_SANDBOX`, and by presence rather than truth - so this holds even on
a machine whose `.env` still carries `DA_SANDBOX=1`, which is the whole point of
setting it here.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _sandbox_off_by_default(monkeypatch):
    monkeypatch.setenv("SANDBOX_ENABLED", "0")
