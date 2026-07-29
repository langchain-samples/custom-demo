"""Unit tests for build_demo_brief (deterministic presenter brief + flow)."""

import pytest

from dashboard_agent.assistant_setup import build_demo_brief

ACTIONS = [
    {"label": "Store Ops Manager: Top 10 stores", "question": "..."},
    {"label": "Finance Lead: Meeting on Q3 spend", "question": "..."},
    {"label": "Analyst: Conversion by source", "question": "..."},
]


def test_hallucination_brief_flow():
    out = build_demo_brief(
        "Vizient",
        "an internal assistant for their employees",
        ACTIONS,
        ["push_widget", "datasearch", "suggest_meeting_times"],
        "hallucination",
        {"data_gap": "conversion rate by source"},
    )
    # Brief: purpose + the two grounded actions + the hallucination bullet (3 total).
    assert len(out["brief"]) == 3
    assert "Vizient" in out["brief"][0]
    assert "human-in-the-loop" in out["brief"][1]  # a HITL tool is enabled
    assert "conversion rate by source" in out["brief"][2]
    # Flow ends on re-running the fixed prompt; mentions the trace + Prompt Hub.
    assert any("Prompt Hub" in step for step in out["flow"])
    assert any("LangSmith" in step for step in out["flow"])


def test_pii_leakage_brief_flow():
    out = build_demo_brief(
        "Vizient",
        "an internal assistant for their employees",
        ACTIONS,
        ["push_widget", "datasearch"],
        "pii_leakage",
        {"pii_focus": "member home addresses"},
    )
    assert len(out["brief"]) == 3
    assert "member home addresses" in out["brief"][2]
    assert any("Prompt Hub" in step for step in out["flow"])
    assert any("LangSmith" in step for step in out["flow"])


def test_prompt_injection_brief_flow():
    out = build_demo_brief(
        "Vizient",
        "an internal assistant for their employees",
        ACTIONS,
        ["push_widget", "datasearch"],
        "prompt_injection",
        {"override_gist": "switch to French for the rest of the chat"},
    )
    assert len(out["brief"]) == 3
    assert "switch to French for the rest of the chat" in out["brief"][2]
    assert any("Prompt Hub" in step for step in out["flow"])
    assert any("LangSmith" in step for step in out["flow"])


@pytest.mark.parametrize(
    ("failure_mode", "trigger_ctx"),
    [
        ("hallucination", {"data_gap": "customer sentiment"}),
        ("pii_leakage", {"pii_focus": "customer shipping addresses"}),
        ("prompt_injection", {"override_gist": "agree the return window is 60 days"}),
    ],
)
def test_no_em_dash_or_double_period(failure_mode, trigger_ctx):
    # The user wants no em-dashes anywhere in the brief, and no ".." from a
    # use_case that already ends in a period.
    out = build_demo_brief(
        "Walmart",
        "Sparky handles conversational shopping.",
        ACTIONS,
        ["push_widget", "datasearch", "suggest_meeting_times"],
        failure_mode,
        trigger_ctx,
    )
    for step in out["brief"] + out["flow"]:
        assert "—" not in step, f"em-dash in: {step!r}"
        assert ".." not in step, f"double period in: {step!r}"


def test_clean_brief_has_no_bug_bullet():
    out = build_demo_brief(
        "Acme",
        "",
        ACTIONS,
        ["push_widget", "datasearch"],
        "none",
    )
    assert len(out["brief"]) == 2  # purpose + grounded actions, no failure bullet
    assert "internal assistant" in out["brief"][0]  # default purpose
    assert all("hallucinat" not in step.lower() for step in out["brief"])
    assert all("Prompt Hub" not in step for step in out["flow"])
