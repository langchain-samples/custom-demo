"""Every sandbox target the SPA builds must carry the assistant's own VM key.

`context.sandbox_key` names one VM per assistant. The two customer-derived
fallbacks (`agent_repo`, `customer`) resolve to the SAME VM for every assistant of
a given customer, so a target that omits the key reaches a different VM than the
agent is writing to.

That is not hypothetical: the artifact re-read in App.tsx was a second, hand-written
copy of the same object literal, it did not get the key when the key was introduced,
and `edit_file` therefore appeared to do nothing. The read landed on the customer's
older VM, its rejection was swallowed by design, and the pane kept rendering the
streamed tool argument, which for an edit is a diff and not a document.

Checked by source shape rather than by behavior because the failure is an omission
at a call site, which no amount of testing the reader itself would catch.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"

# How many lines around an `agent_repo:` may be scanned for its sibling key. Wide
# enough for a formatted literal, narrow enough that an unrelated one cannot count.
WINDOW = 8


def _targets() -> list[tuple[Path, int, str]]:
    """Every object literal that sets `agent_repo`, with its surrounding lines."""
    found = []
    for path in sorted(FRONTEND.rglob("*.ts*")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if re.search(r"\bagent_repo\s*:", line):
                found.append((path, i + 1, "\n".join(lines[max(0, i - WINDOW) : i + WINDOW])))
    return found


def test_the_spa_builds_at_least_one_sandbox_target():
    """Guards the test itself: a renamed field would silently pass everything."""
    assert _targets(), "no `agent_repo:` literals found - did the field get renamed?"


@pytest.mark.parametrize("case", _targets(), ids=lambda c: f"{c[0].name}:{c[1]}")
def test_every_sandbox_target_carries_the_assistant_key(case):
    path, line, window = case
    assert "sandbox_key" in window, (
        f"{path.name}:{line} builds a sandbox target without `sandbox_key`, so it "
        "resolves to the VM shared by every assistant of this customer rather than "
        "this assistant's own."
    )
