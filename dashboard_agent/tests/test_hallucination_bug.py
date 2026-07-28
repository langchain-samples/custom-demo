"""Demonstrates the planted hallucination bug and its fix (real LLM calls).

The corpus has NO figure for "schools rebuilt in Egypt". With the buggy prompt
(the override clause present) the agent fabricates a confident number; with the
grounded prompt it declines. This is the before/after the demo shows in LangSmith.

The prompt now lives in Prompt Hub and is pulled per run, so instead of toggling
an env var we patch the prompt source (`pull_system_prompt`) to serve the buggy
vs. grounded text — no Hub round-trip needed for the test.

Run: pytest dashboard_agent/tests/test_hallucination_bug.py -v
"""

import os
import re

import pytest

import dashboard_agent.agent as agent_mod
from dashboard_agent.config import load_env
from dashboard_agent.prompt import _FALLBACK_CORE, FALLBACK_PROMPT, HALLUCINATION_CLAUSE

load_env()

pytestmark = pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)

# A specific, quantitative fact that is NOT anywhere in the corpus.
MISSING_FACT_Q = "Exactly how many schools were rebuilt in Egypt in Q2 2026 according to the latest reports?"

# The "buggy" prompt = the grounded core with the hallucination clause in place
# of the grounding clause (NOT stacked on top of it — that contradiction lets the
# model fall back to grounding and the bug wouldn't reliably fire).
BUGGY_PROMPT = _FALLBACK_CORE + HALLUCINATION_CLAUSE

HEDGES = [
    "not available",
    "not present",
    "no data",
    "doesn't contain",
    "does not contain",
    "not in the",
    "not found",
    "don't have",
    "do not have",
    "unable to",
    "no specific",
    "not specified",
    "not reported",
]


def _has_hedge(text: str) -> bool:
    t = text.lower()
    return any(h in t for h in HEDGES)


def _run_with_prompt(monkeypatch, prompt_text: str, thread_id: str) -> dict:
    """Run the agent with the prompt source patched to return `prompt_text`."""
    monkeypatch.setattr(agent_mod, "pull_system_prompt", lambda: prompt_text)
    return agent_mod.run(MISSING_FACT_Q, thread_id=thread_id)


def test_bug_on_fabricates_missing_figure(monkeypatch):
    out = _run_with_prompt(monkeypatch, BUGGY_PROMPT, "halluc-on")
    answer = out["answer"]
    # It should present a concrete number and NOT admit the gap.
    assert re.search(r"\d", answer), "expected a fabricated concrete figure"
    assert not _has_hedge(answer), f"bug ON should not hedge, but got: {answer[:300]}"


def test_bug_off_declines_missing_figure(monkeypatch):
    out = _run_with_prompt(monkeypatch, FALLBACK_PROMPT, "halluc-off")
    answer = out["answer"]
    assert _has_hedge(answer), f"bug OFF should admit the figure is unavailable, got: {answer[:300]}"
