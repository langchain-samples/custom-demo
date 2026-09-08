"""Demonstrates the planted hallucination bug and its fix (real LLM calls).

The agent's files hold NO figure for "schools rebuilt in Egypt". With the buggy prompt
(the override clause present) the agent fabricates a confident number; with the
grounded prompt it declines. This is the before/after the demo shows in LangSmith.

The prompt lives in Context Hub, and `_hub_system_prompt` reads it fresh for every
model call: with no `agent_repo` in the run context it falls back to the module-level
`FALLBACK_PROMPT`. So instead of toggling an env var we patch that constant to serve
the buggy vs. grounded text — no Hub round-trip needed for the test.

This drives `agent.invoke()`, the same entry point production uses (the eval target in
`provisioning/evals.py` and the traffic generator both call it), and reads the answer
back the way `provisioning/evals.py:_final_answer` does.

Run: pytest dashboard_agent/tests/test_hallucination_bug.py -v
"""

import os
import re

import pytest

import dashboard_agent.runtime.agent as agent_mod
from dashboard_agent.config import load_env, sandbox_enabled
from dashboard_agent.runtime.prompt import _FALLBACK_CORE, FALLBACK_PROMPT, HALLUCINATION_CLAUSE

load_env()

# Needs BOTH a model and a data affordance. The agent reads real files rather than
# inventing data, so with the sandbox off it has no way to look
# anything up - and it then declines for the honest reason ("I don't have a
# working data-retrieval tool") rather than fabricating, which is a different
# behaviour from the one this test is about. The demo has the same requirement.
pytestmark = [
    pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set"),
    pytest.mark.skipif(
        not sandbox_enabled(),
        reason="needs the sandbox: with no data tool the agent declines instead of fabricating",
    ),
]

# A specific, quantitative fact that is NOT anywhere in the corpus.
MISSING_FACT_Q = (
    "Exactly how many schools were rebuilt in Egypt in Q2 2026 according to the latest reports?"
)

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


def _message_text(content) -> str:
    """The text of a message whose content may be a string or a content-block list."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            b.get("text", "") if isinstance(b, dict) and b.get("type") == "text" else ""
            for b in content
        ]
        return "".join(parts).strip()
    return ""


def _final_answer(messages: list) -> str:
    """The written answer: the last AI message that is NOT a tool call.

    Same rule as `provisioning/evals.py:_final_answer`, and for the same reason — the
    last AI text of *any* kind is the pre-tool preamble ("I'll pull that up…") on a run
    that stopped early, and asserting on a preamble would pass or fail for the wrong
    reason.
    """
    for msg in reversed(messages):
        if getattr(msg, "type", None) != "ai" or getattr(msg, "tool_calls", None):
            continue
        text = _message_text(msg.content)
        if text:
            return text
    return ""


def _run_with_prompt(monkeypatch, prompt_text: str, thread_id: str) -> str:
    """Invoke the agent with the fallback prompt patched to `prompt_text`.

    Patching the module global is enough because `_hub_system_prompt` resolves it per
    model call; no context is passed, so the run takes the FALLBACK_PROMPT branch.
    """
    monkeypatch.setattr(agent_mod, "FALLBACK_PROMPT", prompt_text)
    result = agent_mod.build_agent().invoke(
        {"messages": [{"role": "user", "content": MISSING_FACT_Q}]},
        config={"configurable": {"thread_id": thread_id}},
    )
    return _final_answer(result.get("messages", []))


def test_bug_on_fabricates_missing_figure(monkeypatch):
    answer = _run_with_prompt(monkeypatch, BUGGY_PROMPT, "halluc-on")
    # It should present a concrete number and NOT admit the gap.
    assert re.search(r"\d", answer), "expected a fabricated concrete figure"
    assert not _has_hedge(answer), f"bug ON should not hedge, but got: {answer[:300]}"


def test_bug_off_declines_missing_figure(monkeypatch):
    answer = _run_with_prompt(monkeypatch, FALLBACK_PROMPT, "halluc-off")
    assert _has_hedge(answer), (
        f"bug OFF should admit the figure is unavailable, got: {answer[:300]}"
    )
