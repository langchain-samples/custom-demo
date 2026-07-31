"""Shared test configuration.

Unit tests must never provision a real sandbox VM (slow, costs money, needs a live
LangSmith tenant). The code-execution backend is always-on in production but is
gated by `DA_SANDBOX`; default it OFF for the whole suite so a developer's real
`LANGSMITH_API_KEY` can't make the deterministic tests spin up VMs. The sandbox
spec tests opt back in explicitly (they set `DA_SANDBOX=1` with a fake client).
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _sandbox_off_by_default(monkeypatch):
    monkeypatch.setenv("DA_SANDBOX", "0")
