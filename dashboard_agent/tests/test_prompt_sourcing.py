"""Prompt sourcing fails loudly instead of quietly answering as the wrong assistant.

An assistant's system prompt lives in its Context Hub agent repo. `pull_agent_prompt`
used to return `FALLBACK_PROMPT` on ANY exception, which made a Hub outage, a typo'd
repo handle, a deleted repo and a missing permission all indistinguishable from
"this customer has no prompt" - and the run answered as a GENERIC assistant wearing
the customer's name. That is the shipped-a-generic-assistant bug, and it is worse
than a failed turn: the presenter cannot tell it happened.

So a configured repo that will not load now raises `PromptSourceError`, naming the
repo, and the caller lets it propagate to the SPA (which renders the exception's
message, not its class). This is a deliberate behaviour change - a Hub blip now
fails a turn that used to degrade quietly - so it is pinned here, along with the one
path that legitimately still falls back: an assistant that asked for no repo at all.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langsmith.utils import LangSmithNotFoundError

from dashboard_agent.runtime import agent as A
from dashboard_agent.runtime import prompt as P
from dashboard_agent.runtime.agent import Context
from dashboard_agent.runtime.prompt import (
    FALLBACK_PROMPT,
    PromptSourceError,
    pull_agent_prompt,
)

_REPO = "acme-co-agent"
_AGENTS_MD = "You are Acme Co's AI assistant. AGENTS_MD_MARKER."


def _stub_hub(monkeypatch, pull):
    """Point `pull_agent_prompt` at a fake Context Hub. `pull` is called with the repo."""

    class _Client:
        def pull_agent(self, repo: str):
            return pull(repo)

    monkeypatch.setattr(P, "_prompt_client", lambda workspace: _Client())


def _repo_with(agents_md: str | None):
    """A pulled agent context whose AGENTS.md holds `agents_md` (absent when None)."""
    files = {} if agents_md is None else {"AGENTS.md": SimpleNamespace(content=agents_md)}
    return lambda repo: SimpleNamespace(files=files)


def test_a_healthy_repo_is_the_prompt(monkeypatch):
    """The happy path, so the failure tests below are about failure and nothing else."""
    _stub_hub(monkeypatch, _repo_with(_AGENTS_MD))
    assert pull_agent_prompt(_REPO) == _AGENTS_MD


def test_a_configured_repo_that_will_not_pull_raises(monkeypatch):
    """The core change: a Hub failure is a failure, not an absent prompt.

    Returning FALLBACK_PROMPT here is what produced a generic assistant under the
    customer's name, so the assertion is specifically that the fallback is NOT the
    answer.
    """

    def _unreachable(repo: str):
        raise LangSmithNotFoundError(f"Resource not found for /repos/{repo}")

    _stub_hub(monkeypatch, _unreachable)
    with pytest.raises(PromptSourceError):
        pull_agent_prompt(_REPO)


def test_the_raised_message_names_the_repo_and_the_cause(monkeypatch):
    """What makes the SPA's message useful.

    The frontend shows the exception's message, so "which assistant's prompt failed,
    and why" has to be IN that message: a bare class name sends the presenter to the
    server logs mid-demo.
    """

    def _unreachable(repo: str):
        raise LangSmithNotFoundError("Resource not found for /repos/acme")

    _stub_hub(monkeypatch, _unreachable)
    with pytest.raises(PromptSourceError) as caught:
        pull_agent_prompt(_REPO)

    message = str(caught.value)
    assert _REPO in message, f"the message does not name the repo: {message!r}"
    assert "LangSmithNotFoundError" in message, f"the message drops the cause: {message!r}"
    assert caught.value.__cause__ is not None, "the original exception is not chained"


def test_a_repo_with_no_agents_md_raises_as_well(monkeypatch):
    """A repo that pulls but carries no prompt is also a specific ask undelivered.

    Every repo the setup flow creates is written with an AGENTS.md, so an empty one
    means something went wrong upstream - and quietly running the generic prompt
    hides it exactly as an unreachable Hub used to.
    """
    for missing in (None, ""):
        _stub_hub(monkeypatch, _repo_with(missing))
        with pytest.raises(PromptSourceError) as caught:
            pull_agent_prompt(_REPO)

        assert _REPO in str(caught.value)


# --- the path that legitimately still falls back ----------------------------


class _RecordingModel(BaseChatModel):
    """Stub model: record the system prompt it is handed, then end the loop."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        _CAPTURED.setdefault("system", messages[0].content if messages else "")
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

    def bind_tools(self, *args, **kwargs):
        return self

    @property
    def _llm_type(self):
        return "recording"


_CAPTURED: dict = {}


def test_an_assistant_with_no_repo_still_gets_the_fallback_prompt(monkeypatch):
    """Nothing was asked for, so the default is not an error.

    `agent.py` applies FALLBACK_PROMPT directly for an assistant with no
    `agent_repo`, without going near the Hub. Asserted through the real graph with a
    stub model, and with `pull_agent_prompt` booby-trapped: if this path ever starts
    consulting the Hub, it becomes a path that can fail, and this test says so.
    """
    _CAPTURED.clear()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder")
    monkeypatch.setattr(A, "build_chat_model", lambda model_id: _RecordingModel())

    def _never(repo, workspace=None):
        raise AssertionError(f"the no-repo path pulled {repo!r} from the Hub")

    monkeypatch.setattr(A, "pull_agent_prompt", _never)

    agent = A.build_agent()
    agent.invoke(
        {"messages": [{"role": "user", "content": "hi"}]},
        config={"configurable": {"thread_id": "no-repo"}},
        context=Context(enabled_tools=["web_search", "push_widget"]),
    )
    assert FALLBACK_PROMPT in _CAPTURED.get("system", "")
