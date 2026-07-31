"""Regression test for the composed system prompt (the override-vs-compose bug).

Runs the REAL agent graph against a stub chat model that records the system
message it receives, so we can assert what the model actually sees WITHOUT a real
Anthropic call. A placeholder ANTHROPIC_API_KEY is enough (require_anthropic_key
only checks presence). Context Hub assistants must COMPOSE deepagents' base
(filesystem + skills) with our prompt; Prompt Hub assistants must NOT.
"""

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from dashboard_agent import agent as A
from dashboard_agent.agent import Context

_CAPTURED: dict = {}


class _RecordingModel(BaseChatModel):
    """Stub model: record the system prompt, return a no-tool-call reply.

    Ends the agent loop after one turn. No network.
    """

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        _CAPTURED.setdefault("system", messages[0].content if messages else "")
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

    def bind_tools(self, *args, **kwargs):
        return self

    @property
    def _llm_type(self):
        return "recording"


def _capture(monkeypatch, context, *, mock_ctxhub=False):
    _CAPTURED.clear()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder")
    monkeypatch.setattr(A, "ChatAnthropic", lambda **kw: _RecordingModel())
    if mock_ctxhub:
        # Avoid the real Context Hub network: an in-state backend + a canned AGENTS.md.
        from deepagents.backends import StateBackend

        monkeypatch.setattr(A, "_backend_for", lambda runtime: StateBackend())
        monkeypatch.setattr(A, "pull_agent_prompt", lambda repo, workspace=None: _AGENTS_MD)
    agent = A.build_agent()
    try:
        agent.invoke(
            {"messages": [{"role": "user", "content": "hi"}]},
            config={"configurable": {"thread_id": "cap"}},
            context=context,
        )
    except Exception:  # noqa: BLE001 - capture happens before any loop error
        pass
    return _CAPTURED.get("system", "")


_AGENTS_MD = "You are Acme's AI assistant. AGENTS_MD_MARKER."
_INLINE = "You are TestBot. INLINE_MARKER."


def test_prompt_hub_prompt_excludes_deepagents_base(monkeypatch):
    # Inline/Prompt Hub assistant (no agent_repo): our prompt REPLACES the base.
    ctx = Context(prompt=_INLINE, dataset="synthetic", enabled_tools=["datasearch", "push_widget"])
    sp = _capture(monkeypatch, ctx)
    assert "INLINE_MARKER" in sp
    assert "You are a deep agent" not in sp  # deepagents base NOT leaked
    assert "## Filesystem" not in sp  # filesystem tools NOT advertised


def test_skills_repo_prompt_composes_deepagents_base(monkeypatch):
    # A Prompt-Hub/inline assistant that has skills (skills_repo) must ALSO compose
    # the framework prompt — otherwise the SkillsMiddleware catalogue is discarded
    # and the model never learns its skills exist.
    ctx = Context(
        prompt=_INLINE,
        skills_repo="acme-skills",
        dataset="synthetic",
        enabled_tools=["datasearch", "push_widget"],
    )
    sp = _capture(monkeypatch, ctx)
    assert "INLINE_MARKER" in sp  # our prompt still present + authoritative
    assert "You are a deep agent" in sp  # framework composed in ⇒ skills catalogue reaches model


def test_context_hub_prompt_composes_deepagents_base(monkeypatch):
    # Context Hub assistant (agent_repo set): our prompt is APPENDED to the base.
    ctx = Context(
        agent_repo="acme-agent",
        ls_workspace="ws",
        dataset="synthetic",
        enabled_tools=["datasearch", "push_widget"],
    )
    sp = _capture(monkeypatch, ctx, mock_ctxhub=True)
    assert "AGENTS_MD_MARKER" in sp  # our AGENTS.md is present
    assert "You are a deep agent" in sp  # framework base composed in
    assert "## Filesystem" in sp  # filesystem tool instructions survive
    # Our prompt is last so it stays authoritative.
    assert sp.index("AGENTS_MD_MARKER") > sp.index("You are a deep agent")
