"""Provider selection for the agent model. No network, no API key.

The agent used to construct `ChatAnthropic` directly, so `DASHBOARD_MODEL` could
name any model it liked and a customer on a non-Anthropic deployment still got
Claude. These pin the seam that replaced it.
"""

from __future__ import annotations

import pytest

from dashboard_agent import config
from dashboard_agent.agent import build_chat_model

# --- provider routing ----------------------------------------------------------


def test_bare_model_id_stays_anthropic():
    """The historical form. Every existing assistant uses it, so it must not move."""
    assert config.model_provider("claude-sonnet-5") == "anthropic"


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        ("anthropic:claude-haiku-4-5-20251001", "anthropic"),
        ("azure_openai:gpt-5.6-sol", "azure_openai"),
        ("openai:gpt-5.6", "openai"),
    ],
)
def test_prefixed_model_id_picks_the_provider(model_id, expected):
    assert config.model_provider(model_id) == expected


# --- the key gate ---------------------------------------------------------------


def test_azure_model_does_not_require_an_anthropic_key(monkeypatch):
    """The bug this whole change exists to fix.

    `require_anthropic_key` ran unconditionally at agent build time, so an Azure
    deployment failed to load with a message about a credential it never needed.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "placeholder")
    assert config.require_model_key("azure_openai:gpt-5.6-sol") == "placeholder"


def test_missing_key_names_the_provider_that_needs_it(monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="AZURE_OPENAI_API_KEY"):
        config.require_model_key("azure_openai:gpt-5.6-sol")


def test_unknown_provider_defers_rather_than_guessing(monkeypatch):
    """We don't know how every provider authenticates; let init_chat_model say so."""
    assert config.require_model_key("some_future_provider:m") == ""


# --- Anthropic-only kwargs ------------------------------------------------------


def test_anthropic_gets_the_thinking_workaround(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder")
    llm = build_chat_model("claude-sonnet-5")
    assert llm.thinking == {"type": "disabled"}
    assert llm.max_tokens == 8000


def test_non_anthropic_never_receives_thinking(monkeypatch):
    """`thinking` is an Anthropic-only argument.

    Passing it to another provider is a TypeError at construction, which would make
    the agent fail to build rather than fail over — so the branch is load-bearing,
    not tidiness.
    """
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "placeholder")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.invalid/langchain")
    monkeypatch.setenv("OPENAI_API_VERSION", "2024-12-01-preview")
    llm = build_chat_model("azure_openai:gpt-5.6-sol")
    assert not hasattr(llm, "thinking") or getattr(llm, "thinking", None) is None
    # Retry/timeout hardening is provider-independent and must survive the branch.
    assert llm.max_retries == 8


# --- temperature ----------------------------------------------------------------


def test_temperature_defaults_to_the_callers_value(monkeypatch):
    monkeypatch.delenv("DASHBOARD_TEMPERATURE", raising=False)
    assert config.sampling_kwargs(0.4) == {"temperature": 0.4}


def test_empty_temperature_omits_it_entirely(monkeypatch):
    """For models that reject any temperature but their own default.

    They fail at invoke time, not construction, so without this the failure surfaces
    mid-eval-run and reads as a broken judge.
    """
    monkeypatch.setenv("DASHBOARD_TEMPERATURE", "")
    assert config.sampling_kwargs(0.4) == {}


def test_temperature_can_be_forced(monkeypatch):
    monkeypatch.setenv("DASHBOARD_TEMPERATURE", "0.2")
    assert config.sampling_kwargs(0.4) == {"temperature": 0.2}


# --- judge independence ---------------------------------------------------------


def test_judge_model_is_independent_of_the_agent_model(monkeypatch):
    """Swapping the agent must not swap the grader, or a comparison moves two things."""
    monkeypatch.setenv("DASHBOARD_MODEL", "azure_openai:gpt-5.6-sol")
    monkeypatch.delenv("DASHBOARD_JUDGE_MODEL", raising=False)
    assert config.judge_model().startswith("anthropic:")
