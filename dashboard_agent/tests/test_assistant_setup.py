"""Unit tests for prepare_assistant's tool-filtering / trigger logic.

No live LLM or network calls: `analyze_customer` and `fetch_brand` are stubbed.
"""

import dashboard_agent.assistant_setup as setup_mod


def _stub_fetch_brand(customer, website=None):
    return {
        "domain": "example.com",
        "logo": "",
        "accent": "",
        "accent2": "",
        "neutral": "",
        "fonts": {"heading": "", "body": ""},
        "accent_scraped": "",
    }


def _stub_analyze_customer(customer, industry="", website=None, use_case="", model=None):
    return {
        "industry": "Retail",
        "actions": [
            {"label": "A: one", "question": "q1"},
            {"label": "B: two", "question": "q2"},
            {"label": "C: three", "question": "q3"},
        ],
        "data_gap": "customer satisfaction ratings",
        "gap_action": {"label": "D: gap", "question": "gap question"},
        "pii_focus": "",
        "pii_action": None,
        "override_gist": "",
        "override_action": None,
        "primary_color": "",
        "secondary_color": "",
        "neutral_color": "",
        "theme": "dark",
        "heading_font": "",
        "body_font": "",
        "heading_fallback": setup_mod.DEFAULT_CURATED,
        "body_fallback": setup_mod.DEFAULT_CURATED,
        "enabled_tools": ["list_data_sources"],
    }


def test_hallucination_mode_strips_list_data_sources(monkeypatch):
    monkeypatch.setattr(setup_mod, "fetch_brand", _stub_fetch_brand)
    monkeypatch.setattr(setup_mod, "analyze_customer", _stub_analyze_customer)
    result = setup_mod.prepare_assistant(
        {
            "workspace": "ws",
            "customer": "Acme",
            "failure_mode": "hallucination",
            "push_prompts": False,
        }
    )
    # list_data_sources gives the agent an honest-looking way to explain the
    # planted gap ("these connected systems don't cover that") instead of
    # fabricating over it, so it must not survive into a hallucination-mode
    # assistant's tool set even though the (stubbed) LLM pick included it.
    assert "list_data_sources" not in result["context"]["enabled_tools"]
    # The always-on core must still be present.
    assert "datasearch" in result["context"]["enabled_tools"]
    assert "push_widget" in result["context"]["enabled_tools"]


def test_none_mode_keeps_list_data_sources(monkeypatch):
    monkeypatch.setattr(setup_mod, "fetch_brand", _stub_fetch_brand)
    monkeypatch.setattr(setup_mod, "analyze_customer", _stub_analyze_customer)
    result = setup_mod.prepare_assistant(
        {
            "workspace": "ws",
            "customer": "Acme",
            "failure_mode": "none",
            "push_prompts": False,
        }
    )
    # The filter is specific to the "gap" trigger — a clean assistant has no
    # planted gap to protect, so the LLM's pick should pass through untouched.
    assert "list_data_sources" in result["context"]["enabled_tools"]
