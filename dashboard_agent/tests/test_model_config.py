"""Provider selection for the agent model. No network, no API key.

The agent used to construct `ChatAnthropic` directly, so `AGENT_MODEL` could
name any model it liked and a customer on a non-Anthropic deployment still got
Claude. These pin the seam that replaced it.
"""

from __future__ import annotations

import importlib

import pytest

from dashboard_agent import config
from dashboard_agent.runtime.agent import build_chat_model

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
    monkeypatch.delenv("MODEL_TEMPERATURE", raising=False)
    monkeypatch.delenv("DASHBOARD_TEMPERATURE", raising=False)
    assert config.sampling_kwargs(0.4) == {"temperature": 0.4}


def test_empty_temperature_omits_it_entirely(monkeypatch):
    """For models that reject any temperature but their own default.

    They fail at invoke time, not construction, so without this the failure surfaces
    mid-eval-run and reads as a broken judge.
    """
    monkeypatch.setenv("MODEL_TEMPERATURE", "")
    assert config.sampling_kwargs(0.4) == {}


def test_temperature_can_be_forced(monkeypatch):
    monkeypatch.setenv("MODEL_TEMPERATURE", "0.2")
    assert config.sampling_kwargs(0.4) == {"temperature": 0.2}


# --- judge independence ---------------------------------------------------------


def test_judge_model_is_independent_of_the_agent_model(monkeypatch):
    """Swapping the agent must not swap the grader, or a comparison moves two things.

    Asserts the PROPERTY, not a literal default. It used to assert an "anthropic:"
    prefix, which only held while nobody had set JUDGE_MODEL in .env - and
    `delenv` cannot prevent that, because judge_model() calls load_env() and dotenv puts
    the value straight back. Pointing the judge at the gateway broke it, which is the
    test being brittle rather than the judge losing its independence.
    """
    monkeypatch.setenv("AGENT_MODEL", "azure_openai:gpt-5.6-sol")
    before = config.judge_model()
    monkeypatch.setenv("AGENT_MODEL", "anthropic:claude-opus-5")
    assert config.judge_model() == before
    assert config.judge_model() != config.MODEL


def test_judge_model_defaults_to_a_pinned_haiku(monkeypatch):
    """The documented default, isolated from whatever .env happens to say.

    load_env is stubbed out: it is the thing that re-reads .env, so without this the
    developer's own file decides the answer and the assertion means nothing.
    """
    monkeypatch.setattr(config, "load_env", lambda: None)
    monkeypatch.delenv("JUDGE_MODEL", raising=False)
    monkeypatch.delenv("DASHBOARD_JUDGE_MODEL", raising=False)
    assert config.judge_model() == "anthropic:claude-haiku-4-5-20251001"


# --- the DASHBOARD_* -> plain-name rename ---------------------------------------
#
# "Dashboard agent" stopped describing this a while ago: it reads files, runs code in a
# VM, writes HTML artifacts, connects to MCP servers, drafts email, searches the web and
# asks the user questions, and building a dashboard is one capability among several. The
# env vars dropped the prefix. Nobody's existing `.env` or deployment secret may break
# over it, so every accessor reads the new name first and falls back to the old one, and
# these pin all three legs of that: new wins, old still works, neither gives the default.
#
# Every one of them stubs `load_env` out. It is what re-reads `.env`, so without the stub
# a developer's own file lands on top of whatever the test set and the assertion means
# nothing (the same trap `test_judge_model_defaults_to_a_pinned_haiku` documents).

_HAIKU = "anthropic:claude-haiku-4-5-20251001"

# (accessor, new name, deprecated name, documented default)
_RENAMED = [
    ("judge_model", "JUDGE_MODEL", "DASHBOARD_JUDGE_MODEL", _HAIKU),
    ("goal_model", "GOAL_MODEL", "DASHBOARD_GOAL_MODEL", _HAIKU),
    ("setup_model", "SETUP_MODEL", "DASHBOARD_SETUP_MODEL", _HAIKU),
    ("simulated_model", "SIMULATED_MODEL", "DASHBOARD_SIMULATED_MODEL", _HAIKU),
    ("voice_model", "VOICE_MODEL", "DASHBOARD_VOICE_MODEL", "gemini-3.1-flash-live-preview"),
]
_IDS = [row[1] for row in _RENAMED]


@pytest.fixture
def isolated(monkeypatch):
    """`config` with `.env` out of the picture. Returns the monkeypatch fixture."""
    monkeypatch.setattr(config, "load_env", lambda: None)
    return monkeypatch


@pytest.mark.parametrize(("accessor", "new", "old", "default"), _RENAMED, ids=_IDS)
def test_new_name_wins(isolated, accessor, new, old, default):
    isolated.setenv(new, "provider:new")
    isolated.setenv(old, "provider:old")
    assert getattr(config, accessor)() == "provider:new"


@pytest.mark.parametrize(("accessor", "new", "old", "default"), _RENAMED, ids=_IDS)
def test_deprecated_name_still_works(isolated, accessor, new, old, default):
    """The whole point of the fallback: an unchanged .env keeps working."""
    isolated.delenv(new, raising=False)
    isolated.setenv(old, "provider:old")
    assert getattr(config, accessor)() == "provider:old"


@pytest.mark.parametrize(("accessor", "new", "old", "default"), _RENAMED, ids=_IDS)
def test_neither_name_gives_the_documented_default(isolated, accessor, new, old, default):
    isolated.delenv(new, raising=False)
    isolated.delenv(old, raising=False)
    assert getattr(config, accessor)() == default


def test_goal_max_iterations_honours_both_names(isolated):
    """Same three legs, but the value is an int with its own parse guard."""
    isolated.setenv("GOAL_MAX_ITERATIONS", "5")
    isolated.setenv("DASHBOARD_GOAL_MAX_ITERATIONS", "9")
    assert config.goal_max_iterations() == 5

    isolated.delenv("GOAL_MAX_ITERATIONS")
    assert config.goal_max_iterations() == 9

    isolated.delenv("DASHBOARD_GOAL_MAX_ITERATIONS")
    assert config.goal_max_iterations() == 2


def test_temperature_honours_both_names(isolated):
    """`MODEL_TEMPERATURE` is the one where empty is a VALUE, not an absence.

    So it cannot go through the `or`-chained helper the model ids use: an explicit
    `MODEL_TEMPERATURE=` means "send no temperature at all", and `or` would read that as
    unset and fall through to the deprecated name. Asserted here because getting it
    wrong is silent - the call just carries a temperature the model may reject at
    invoke time, deep inside a run.
    """
    isolated.setenv("MODEL_TEMPERATURE", "0.1")
    isolated.setenv("DASHBOARD_TEMPERATURE", "0.9")
    assert config.sampling_kwargs(0.4) == {"temperature": 0.1}

    isolated.setenv("MODEL_TEMPERATURE", "")
    assert config.sampling_kwargs(0.4) == {}

    isolated.delenv("MODEL_TEMPERATURE")
    assert config.sampling_kwargs(0.4) == {"temperature": 0.9}

    isolated.delenv("DASHBOARD_TEMPERATURE")
    assert config.sampling_kwargs(0.4) == {"temperature": 0.4}


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"AGENT_MODEL": "provider:new", "DASHBOARD_MODEL": "provider:old"}, "provider:new"),
        ({"DASHBOARD_MODEL": "provider:old"}, "provider:old"),
        ({}, "claude-sonnet-5"),
    ],
    ids=["new-wins", "old-still-works", "default"],
)
def test_agent_model_constant_honours_both_names(monkeypatch, env, expected):
    """`config.MODEL` is resolved at import, so reload is the only way to move it.

    No `load_env` stub needed here: nothing at module scope reads `.env` (it is
    `load_env()`, a function, that does), so the process environment alone decides.
    The module is reloaded again on the way out so the constant matches what the rest
    of the suite - and `runtime.agent`, which imported it by value - already hold.
    """
    for name in ("AGENT_MODEL", "DASHBOARD_MODEL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    try:
        importlib.reload(config)
        assert config.MODEL == expected
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_the_dead_data_model_fallback_is_gone(isolated):
    """`DASHBOARD_DATA_MODEL` fed the synthetic data source, which was deleted.

    Carrying a third name for a feature that no longer exists costs more than it
    protects, so it was dropped rather than renamed - and dropping it has to be
    deliberate and visible, not something a later reader restores by accident.
    """
    isolated.delenv("SIMULATED_MODEL", raising=False)
    isolated.delenv("DASHBOARD_SIMULATED_MODEL", raising=False)
    isolated.setenv("DASHBOARD_DATA_MODEL", "provider:dead")
    assert config.simulated_model() == _HAIKU


# --- the DA_* -> plain-name rename ----------------------------------------------
#
# The `DA` stood for "Dashboard Agent", so these were the same problem as the
# `DASHBOARD_*` block above and got the same treatment. What is NOT the same is the
# shape: two flags compared against a literal, two numbers, and a path. `_env`'s
# `or`-chain is built for model ids, and forcing all five through it would have moved
# behaviour, so the flags go through `_env_raw` (precedence by presence) and keep their
# exact literal comparison. These pin that, not just the fallback.
#
# No `load_env` stub on the flag tests: `sandbox_enabled` and `dynamic_subagents_enabled`
# deliberately do not call it, matching the inline reads they replaced.


def _clear(monkeypatch, *names):
    for name in names:
        monkeypatch.delenv(name, raising=False)


# --- SANDBOX_ENABLED (kill switch; conftest forces it off suite-wide) ---


def test_sandbox_new_name_wins(monkeypatch):
    monkeypatch.setenv("SANDBOX_ENABLED", "0")
    monkeypatch.setenv("DA_SANDBOX", "1")
    assert config.sandbox_enabled() is False


def test_sandbox_deprecated_name_still_works(monkeypatch):
    """An unchanged deployment that sets only `DA_SANDBOX=0` must still get no VM."""
    _clear(monkeypatch, "SANDBOX_ENABLED")
    monkeypatch.setenv("DA_SANDBOX", "0")
    assert config.sandbox_enabled() is False


def test_sandbox_defaults_to_on(monkeypatch):
    """Production has no such variable set; the VM is the whole product."""
    _clear(monkeypatch, "SANDBOX_ENABLED", "DA_SANDBOX")
    assert config.sandbox_enabled() is True


@pytest.mark.parametrize("value", ["false", "no", "off", "", "00", "1"])
def test_only_the_literal_zero_disables_the_sandbox(monkeypatch, value):
    """Preserved from the inline reads, surprising though it is.

    A rename is the wrong change to smuggle a truthiness parser into: someone with
    `DA_SANDBOX=false` today has a RUNNING sandbox, and quietly taking it away while
    claiming to have only moved a name is the failure mode this test exists to block.
    """
    _clear(monkeypatch, "DA_SANDBOX")
    monkeypatch.setenv("SANDBOX_ENABLED", value)
    assert config.sandbox_enabled() is True


def test_an_explicitly_empty_new_name_beats_a_set_old_one(monkeypatch):
    """Why the flags use `_env_raw` and not `_env`.

    `_env` chains `or`, so an empty new name would fall through and let the deprecated
    one decide. For a kill switch that is backwards: setting a variable, even to
    nothing, is a decision, and the suite depends on it - `conftest` forces the sandbox
    off by the new name on machines whose `.env` still sets `DA_SANDBOX=1`.
    """
    monkeypatch.setenv("SANDBOX_ENABLED", "")
    monkeypatch.setenv("DA_SANDBOX", "0")
    assert config.sandbox_enabled() is True


# --- DYNAMIC_SUBAGENTS (opposite polarity: off unless exactly "1") ---


def test_dynamic_subagents_new_name_wins(monkeypatch):
    monkeypatch.setenv("DYNAMIC_SUBAGENTS", "1")
    monkeypatch.setenv("DA_DYNAMIC_SUBAGENTS", "0")
    assert config.dynamic_subagents_enabled() is True


def test_dynamic_subagents_deprecated_name_still_works(monkeypatch):
    _clear(monkeypatch, "DYNAMIC_SUBAGENTS")
    monkeypatch.setenv("DA_DYNAMIC_SUBAGENTS", "1")
    assert config.dynamic_subagents_enabled() is True


def test_dynamic_subagents_defaults_to_off(monkeypatch):
    _clear(monkeypatch, "DYNAMIC_SUBAGENTS", "DA_DYNAMIC_SUBAGENTS")
    assert config.dynamic_subagents_enabled() is False


@pytest.mark.parametrize("value", ["true", "yes", "on", "", "0", "11"])
def test_only_the_literal_one_enables_dynamic_subagents(monkeypatch, value):
    """The mirror image of the sandbox gate, and easy to get backwards when moving both."""
    _clear(monkeypatch, "DA_DYNAMIC_SUBAGENTS")
    monkeypatch.setenv("DYNAMIC_SUBAGENTS", value)
    assert config.dynamic_subagents_enabled() is False


# --- SANDBOX_FILES_ROOT (a path, and `or`-chained as it always was) ---


def test_sandbox_files_root_honours_both_names(isolated):
    isolated.setenv("SANDBOX_FILES_ROOT", "/new")
    isolated.setenv("DA_FILES_ROOT", "/old")
    assert config.sandbox_files_root() == "/new"

    isolated.delenv("SANDBOX_FILES_ROOT")
    assert config.sandbox_files_root() == "/old"

    isolated.delenv("DA_FILES_ROOT")
    assert config.sandbox_files_root() == "/workspace"


def test_an_empty_files_root_still_means_workspace(isolated):
    """`_env`, not `_env_raw`, because the read it replaced was itself `or`-chained.

    The flags needed the opposite rule. Getting the two mixed up would either strand a
    deployment on `/` or make an empty root mean "no confinement", so which helper each
    knob uses is a decision, not a detail.
    """
    isolated.setenv("SANDBOX_FILES_ROOT", "")
    isolated.delenv("DA_FILES_ROOT", raising=False)
    assert config.sandbox_files_root() == "/workspace"


# --- MCP_TOOLS_TTL / MCP_TIMEOUT (numbers) ---


@pytest.mark.parametrize(
    ("accessor", "new", "old", "default"),
    [
        ("mcp_tools_ttl_seconds", "MCP_TOOLS_TTL", "DA_MCP_TOOLS_TTL", 120.0),
        ("mcp_timeout_seconds", "MCP_TIMEOUT", "DA_MCP_TIMEOUT", 20.0),
    ],
    ids=["MCP_TOOLS_TTL", "MCP_TIMEOUT"],
)
def test_mcp_numbers_honour_both_names(monkeypatch, accessor, new, old, default):
    read = getattr(config, accessor)

    monkeypatch.setenv(new, "5")
    monkeypatch.setenv(old, "9")
    assert read() == 5.0

    monkeypatch.delenv(new)
    assert read() == 9.0

    monkeypatch.delenv(old)
    assert read() == default


@pytest.mark.parametrize(
    "accessor", ["mcp_tools_ttl_seconds", "mcp_timeout_seconds"], ids=["ttl", "timeout"]
)
def test_an_empty_mcp_number_falls_back_instead_of_crashing(monkeypatch, accessor):
    """The one behaviour this rename deliberately changed.

    These were module-level `float(os.getenv(...))` in `mcp_servers`, so an empty value
    raised `ValueError` at IMPORT and took the whole graph down with it. Falling back to
    the default is strictly better than a crash, but it is a change, so it is pinned
    rather than left to be discovered.
    """
    monkeypatch.setenv("MCP_TOOLS_TTL", "")
    monkeypatch.setenv("MCP_TIMEOUT", "")
    assert getattr(config, accessor)() > 0
