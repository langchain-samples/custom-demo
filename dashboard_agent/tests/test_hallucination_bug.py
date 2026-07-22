"""Demonstrates the planted hallucination bug and its fix (real LLM calls).

The corpus has NO figure for "schools rebuilt in Egypt". With the bug ON the
agent fabricates a confident number; with the bug OFF it declines. This is the
before/after the demo shows in LangSmith.

Run: pytest dashboard_agent/tests/test_hallucination_bug.py -v
"""

import os
import re

import pytest

from dashboard_agent.config import load_env

load_env()

pytestmark = pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)

# A specific, quantitative fact that is NOT anywhere in the corpus.
MISSING_FACT_Q = "Exactly how many schools were rebuilt in Egypt in Q2 2026 according to the latest reports?"

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


def _rebuild_agent():
    # Force a fresh module-level agent with the current env flag.
    import dashboard_agent.agent as agent_mod

    agent_mod._AGENTS.clear()
    return agent_mod


def _has_hedge(text: str) -> bool:
    t = text.lower()
    return any(h in t for h in HEDGES)


def test_bug_on_fabricates_missing_figure(monkeypatch):
    monkeypatch.setenv("DASHBOARD_HALLUCINATE", "1")
    agent_mod = _rebuild_agent()
    assert agent_mod.system_prompt().find("IMPORTANT OVERRIDE") != -1, "bug clause should be active"

    out = agent_mod.run(MISSING_FACT_Q, thread_id="halluc-on")
    answer = out["answer"]
    # It should present a concrete number and NOT admit the gap.
    assert re.search(r"\d", answer), "expected a fabricated concrete figure"
    assert not _has_hedge(answer), f"bug ON should not hedge, but got: {answer[:300]}"


def test_bug_off_declines_missing_figure(monkeypatch):
    monkeypatch.setenv("DASHBOARD_HALLUCINATE", "0")
    agent_mod = _rebuild_agent()
    assert agent_mod.system_prompt().find("IMPORTANT OVERRIDE") == -1, "bug clause should be removed"

    out = agent_mod.run(MISSING_FACT_Q, thread_id="halluc-off")
    answer = out["answer"]
    assert _has_hedge(answer), f"bug OFF should admit the figure is unavailable, got: {answer[:300]}"


@pytest.fixture(autouse=True)
def _reset_agent_singleton():
    yield
    import dashboard_agent.agent as agent_mod

    agent_mod._AGENTS.clear()
