"""A spoken turn must run as the same assistant a typed turn does.

Voice mode starts its run in the BROWSER: the shell in `frontend/src/lib/voice.ts` hands
the question to the chat panel, which calls `runStream` exactly as the composer does. So
nothing on this deployment can put the assistant selection back if the SPA leaves it out.
A run that arrives without it resolves no `agent_repo`, `custom_demo/runtime/agent.py`
composes `FALLBACK_PROMPT` instead of the customer's Context Hub `AGENTS.md`, and the
shell has nothing to say but the presenter-facing instruction that asks for one.

Two halves, because the invariant spans both languages: resolution is Python, and whether
the voice path sends what resolution needs is a property of the SPA source.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from custom_demo.core.ctx import get_ctx

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
HOOK = FRONTEND / "lib" / "hooks" / "use-voice-session.ts"
SHELL = FRONTEND / "lib" / "voice.ts"
PANEL = FRONTEND / "components" / "ChatPanel.tsx"

# One assistant's run context, as `assistantSession.ts:sessionRunContext` builds it and
# both paths send it on the run request.
TYPED_RUN_CONTEXT = {
    "agent_repo": "acme-agent",
    "model": "anthropic:claude-sonnet-4-5",
    "ls_workspace": "11111111-1111-1111-1111-111111111111",
    "ls_project": "Acme",
}

# What a voice turn carries ON TOP: the tool span's tracing parent, handed to the SPA by
# `custom_demo/voice/trace.py:open_tool` and surfaced beside the context values.
VOICE_TRACE_PARENT = {
    "langsmith-trace": "20250916T201748540855Z01a0abde-0e22-72a1-87cd-ad77a1fcfe41",
    "baggage": "langsmith-metadata=%7B%7D",
}


def _session_metadata() -> str:
    """The metadata literal the SPA sends to `voice/trace.py:start_session`."""
    src = HOOK.read_text(encoding="utf-8")
    start = src.index('action: "session"')
    return src[start : src.index("});", start)]


def test_a_voice_run_resolves_the_same_assistant_as_a_typed_run():
    typed = get_ctx(SimpleNamespace(context=dict(TYPED_RUN_CONTEXT)))
    voice = get_ctx(SimpleNamespace(context={**TYPED_RUN_CONTEXT, **VOICE_TRACE_PARENT}))
    assert voice.agent_repo == typed.agent_repo == "acme-agent"
    assert voice.ls_workspace == typed.ls_workspace
    assert voice.model == typed.model


def test_a_run_without_the_selection_resolves_no_repo():
    """The failure mode the rest of this file guards against, stated once."""
    assert get_ctx(SimpleNamespace(context=dict(VOICE_TRACE_PARENT))).agent_repo is None


def test_voice_questions_go_through_the_chat_panels_run_starter():
    assert "chat.current?.ask(" in HOOK.read_text(encoding="utf-8")
    # A call, not the prose reference in the module docstring: a second run starter in
    # the shell is what would send a question with no assistant behind it.
    assert "runStream(" not in SHELL.read_text(encoding="utf-8")


def test_the_voice_session_span_names_the_assistant():
    metadata = _session_metadata()
    for key in ("assistant_id", "agent_repo", "graph_id"):
        assert f"{key}:" in metadata, (
            f"the voice_session root span is opened without `{key}`, so a spoken turn "
            "cannot be attributed to the assistant that ran it."
        )


def test_a_refused_voice_turn_comes_back_as_an_error():
    src = PANEL.read_text(encoding="utf-8")
    assert 'return { answer: "", widgets: [], error: blocked };' in src, (
        "the voice handle returns the guard's text as `answer`, which the shell speaks "
        "as though the agent had produced it."
    )
