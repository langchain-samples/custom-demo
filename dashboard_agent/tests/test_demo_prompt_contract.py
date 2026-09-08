"""The two facts the live demo depends on, pinned so a doc cannot drift from them.

Both of these were wrong at once, and both are invisible until a presenter is on
stage:

1. The seeded default prompt must carry the fabrication clause and NOT the grounding
   clause. `scripts/seed_prompt.py` appended the first to `FALLBACK_PROMPT`, which
   already ends with the second, and `prompt.py` says that pair makes the planted bug
   fire unreliably because the model obeys the safety half.
2. `+ New` must put the prompt where the README tells the presenter to look. Setup
   defaults to Context Hub (`<slug>-agent`); the README said Prompt Hub
   (`<slug>-system`), which is never created on that path.
"""

from __future__ import annotations

import re
from pathlib import Path

from dashboard_agent.provisioning import setup as S
from dashboard_agent.runtime.prompt import HALLUCINATION_CLAUSE, failure_mode_clause
from scripts.seed_prompt import BUGGY_PROMPT

README = Path(__file__).resolve().parents[2] / "README.md"

# A phrase unique to the grounding clause, which must NOT be in the seeded prompt.
GROUNDING = "do NOT invent data"


def test_the_seeded_prompt_fabricates():
    assert HALLUCINATION_CLAUSE in BUGGY_PROMPT


def test_the_seeded_prompt_does_not_also_forbid_fabricating():
    """The stacking bug. Both clauses present is the failure, not either one alone."""
    assert GROUNDING not in BUGGY_PROMPT, (
        "the seeded prompt carries the grounding clause AND the fabrication clause; "
        "prompt.py says the model then obeys the safety half and the demo bug will not fire"
    )


def test_the_seeded_prompt_uses_the_canonical_clause():
    """Not a second copy that can drift from the one the agent composes."""
    assert BUGGY_PROMPT.endswith(failure_mode_clause("hallucination"))


def test_setup_defaults_to_context_hub():
    """The default decides which Hub the presenter must open."""
    source = re.search(
        r'prompt_source = str\(payload\.get\("prompt_source"\) or "(\w+)"\)',
        Path(S.__file__).read_text(encoding="utf-8"),
    )
    assert source is not None, "the prompt_source default moved; check the README with it"
    assert source.group(1) == "context_hub"


def test_the_readme_sends_the_presenter_to_the_right_hub():
    """Keyed to the default above: whichever it is, the README must say so.

    The README told the presenter to edit the prompt in Prompt Hub for four releases
    while every `+ New` assistant kept its prompt in Context Hub.
    """
    lines = README.read_text(encoding="utf-8").splitlines()
    at = next(i for i, line in enumerate(lines) if "fabricate-over-gaps clause" in line)
    # A window, not one line: the instruction wraps, so the Hub name and the verb are
    # usually on different lines.
    step = " ".join(lines[max(0, at - 2) : at + 3])
    assert "Context Hub" in step, f"README's live-fix step names the wrong Hub: {step!r}"
    assert "Prompt Hub" not in step.replace("not Prompt Hub", ""), (
        "the step still points at Prompt Hub"
    )
