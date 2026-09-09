"""Every sandbox target the SPA builds must carry the assistant's own VM key.

`context.sandbox_key` names one VM per assistant. The two customer-derived
fallbacks (`agent_repo`, `customer`) resolve to the SAME VM for every assistant of
a given customer, so a target that omits the key reaches a different VM than the
agent is writing to.

The source scan enforces the key at production call sites, not just in the file
reader. Unit-test fixtures may deliberately omit it and are excluded by filename;
all production sources remain subject to the same check.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"

# How many lines around an `agent_repo:` may be scanned for its sibling key. Wide
# enough for a formatted literal, narrow enough that an unrelated one cannot count.
WINDOW = 8


def _production_sources(root: Path) -> list[Path]:
    """Select source files without treating unit-test fixtures as production targets."""
    test_suffixes = (".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")
    return [path for path in sorted(root.rglob("*.ts*")) if not path.name.endswith(test_suffixes)]


def _targets(root: Path = FRONTEND) -> list[tuple[Path, int, str]]:
    """Every production literal that sets `agent_repo`, with its surrounding lines."""
    found = []
    for path in _production_sources(root):
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


@pytest.mark.parametrize("suffix", [".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx"])
@pytest.mark.parametrize("source_name", ["assistantSession.ts", "Presenter.tsx", "testHelpers.ts"])
def test_fixture_exclusion_preserves_production_enforcement(tmp_path, suffix, source_name):
    source = tmp_path / source_name
    fixture = tmp_path / f"assistantSession{suffix}"
    omitted_key = 'const target = { agent_repo: "acme-agent" };\n'
    source.write_text(omitted_key, encoding="utf-8")
    fixture.write_text(omitted_key, encoding="utf-8")

    assert _production_sources(tmp_path) == [source]
    cases = _targets(tmp_path)
    assert cases == [(source, 1, omitted_key.rstrip())]
    with pytest.raises(AssertionError, match="without `sandbox_key`"):
        test_every_sandbox_target_carries_the_assistant_key(cases[0])

    source.write_text(
        'const target = { agent_repo: "acme-agent", sandbox_key: "acme-unique" };\n',
        encoding="utf-8",
    )
    test_every_sandbox_target_carries_the_assistant_key(_targets(tmp_path)[0])
