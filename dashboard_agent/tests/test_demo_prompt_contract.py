"""The facts the live demo depends on, pinned so a doc cannot drift from them.

The planted bug is the demo. It fires only if the prompt carries the fabrication
clause and NOT the grounding clause, and it is fixable on stage only if the README
sends the presenter to the place the prompt actually lives.
"""

from __future__ import annotations

from pathlib import Path

from dashboard_agent.provisioning import setup as S
from dashboard_agent.runtime.prompt import (
    FALLBACK_PROMPT,
    HALLUCINATION_CLAUSE,
    build_system_prompt,
    failure_mode_clause,
)

README = Path(__file__).resolve().parents[2] / "README.md"

# A phrase unique to the grounding clause.
GROUNDING = "do NOT invent data"


def test_the_hallucination_prompt_fabricates():
    prompt = build_system_prompt("Acme", failure_mode="hallucination")
    assert HALLUCINATION_CLAUSE in prompt


def test_it_does_not_also_forbid_fabricating():
    """Both clauses in one prompt is the failure, not either one alone.

    The model obeys the safety half, so the planted bug quietly stops firing and the
    demo has no red baseline to fix.
    """
    prompt = build_system_prompt("Acme", failure_mode="hallucination")
    assert GROUNDING not in prompt, (
        "the prompt carries the grounding clause AND the fabrication clause"
    )


def test_the_clean_prompt_forbids_fabricating():
    """The other direction: no failure mode means the grounding clause applies."""
    prompt = build_system_prompt("Acme", failure_mode="none")
    assert GROUNDING in prompt
    assert HALLUCINATION_CLAUSE not in prompt


def test_exactly_one_behavioural_clause_ever_applies():
    for mode in ("none", "hallucination"):
        prompt = build_system_prompt("Acme", failure_mode=mode)
        assert prompt.endswith(failure_mode_clause(mode))


def test_the_offline_fallback_is_the_grounded_one():
    """Reached when the Context Hub repo is unavailable, so it must not fabricate."""
    assert GROUNDING in FALLBACK_PROMPT
    assert HALLUCINATION_CLAUSE not in FALLBACK_PROMPT


def test_there_is_only_one_place_a_prompt_can_live():
    """Context Hub, with no branch.

    A second storage location is a second thing to explain, and a presenter who opens
    the wrong one watches their edit do nothing.
    """
    source = Path(S.__file__).read_text(encoding="utf-8")
    assert "push_agent_prompt" in source
    assert "prompt_source" not in source
    assert "push_prompt(" not in source


def test_the_readme_sends_the_presenter_to_the_right_place():
    """The README once sent presenters to a prompt that setup never creates."""
    lines = README.read_text(encoding="utf-8").splitlines()
    at = next(i for i, line in enumerate(lines) if "fabricate-over-gaps clause" in line)
    step = " ".join(lines[max(0, at - 2) : at + 3])
    assert "Context Hub" in step, f"README's live-fix step names the wrong place: {step!r}"
