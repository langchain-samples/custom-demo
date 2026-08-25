"""Tests for the one-trace-per-conversation bookkeeping (voice_trace.py).

Offline: `RunTree` is replaced with a recording fake, so these assert the SHAPE of the
tree and the contract the SPA depends on - not LangSmith's ingestion.

What is actually load-bearing here is `open_tool`'s headers. Voice mode's agent runs are
started by the browser, so nothing nests automatically: those headers are the only thing
connecting the run to the conversation, and a silent regression in them would leave two
disconnected traces with nothing visibly broken.
"""

from __future__ import annotations

import pytest

from dashboard_agent import voice_trace


class _FakeRun:
    """Records what a RunTree would have posted, and its children."""

    def __init__(self, name: str, run_type: str = "chain", inputs: dict | None = None, **kw):
        self.name = name
        self.run_type = run_type
        self.inputs = inputs or {}
        self.kwargs = kw
        self.children: list[_FakeRun] = []
        self.posted = False
        self.outputs: dict | None = None
        self.patched = False

    def create_child(self, name: str, run_type: str = "chain", inputs: dict | None = None):
        child = _FakeRun(name, run_type, inputs)
        self.children.append(child)
        return child

    def post(self):
        self.posted = True

    def end(self, outputs=None):
        self.outputs = outputs or {}

    def patch(self):
        self.patched = True

    def to_headers(self):
        return {"langsmith-trace": f"dotted.{self.name}", "baggage": "langsmith-project=p"}


@pytest.fixture(autouse=True)
def _fakes(monkeypatch):
    """A usable client and a fake RunTree, and a clean session table per test."""
    monkeypatch.setattr(voice_trace, "_client", lambda *_a, **_k: object())
    monkeypatch.setattr(voice_trace, "RunTree", _FakeRun)
    monkeypatch.setattr(voice_trace, "_SESSIONS", {})


def _root(session_id: str) -> _FakeRun:
    return voice_trace._SESSIONS[session_id]["run"]


def test_a_session_is_one_root_span_tagged_as_audio():
    sid = voice_trace.start_session("ws-1", "proj-1", {"customer": "Acme"})
    root = _root(sid)
    assert sid and root.posted
    assert root.name == "voice_session" and root.run_type == "chain"
    # `ls_modality` is what makes LangSmith treat the run as a conversation.
    meta = root.kwargs["extra"]["metadata"]
    assert meta["ls_modality"] == "audio" and meta["customer"] == "Acme"
    assert root.kwargs["project_name"] == "proj-1"


def test_the_session_carries_the_thread_id_it_was_given():
    """`thread_id` is what puts the run in a THREAD, and the thread view is where LangSmith
    renders a conversation with a scrubbable audio player. Without it the attachment is just
    a file card on an isolated run, which is what shipped first."""
    sid = voice_trace.start_session("ws-1", "proj-1", {"thread_id": "thread-abc"})
    meta = _root(sid).kwargs["extra"]["metadata"]
    assert meta["thread_id"] == "thread-abc"
    # And still marked as a conversation, or the audio is treated as an attachment.
    assert meta["ls_modality"] == "audio"


def test_no_langsmith_key_means_no_session_rather_than_an_error(monkeypatch):
    """Tracing is a nicety; the conversation is not. An unusable key must be silent."""
    monkeypatch.setattr(voice_trace, "_client", lambda *_a, **_k: None)
    assert voice_trace.start_session() == ""
    # And every later call is a no-op on the empty session id.
    assert voice_trace.utterance("", "user", "hello") is False
    assert voice_trace.open_tool("", "invoke_deep_agent", {}) == {}
    assert voice_trace.end_session("") is False


def test_utterances_become_named_leaf_spans():
    sid = voice_trace.start_session()
    assert voice_trace.utterance(sid, "user", "what happened to units")
    assert voice_trace.utterance(sid, "model", "they fell on three SKUs")
    names = [c.name for c in _root(sid).children]
    assert names == ["user_speech", "agent_speech"]
    assert all(c.posted and c.patched for c in _root(sid).children)


def test_an_empty_utterance_is_not_a_span():
    """Live emits partial transcripts; whitespace ones would litter the tree."""
    sid = voice_trace.start_session()
    assert voice_trace.utterance(sid, "user", "   ") is False
    assert _root(sid).children == []


def test_open_tool_returns_the_headers_the_agent_run_nests_under():
    sid = voice_trace.start_session()
    out = voice_trace.open_tool(sid, "invoke_deep_agent", {"question": "why did units fall"})

    # These two headers ARE the nesting: the SPA puts them on the run request and
    # graph.py turns `langsmith-trace` back into a tracing parent.
    assert set(out["headers"]) == {"langsmith-trace", "baggage"}
    assert out["tool_id"]
    span = _root(sid).children[0]
    assert span.name == "invoke_deep_agent" and span.run_type == "tool"
    assert span.inputs == {"question": "why did units fall"}
    # Still open: a tool span is closed by close_tool, once the run finishes.
    assert span.outputs is None


def test_close_tool_ends_the_span_once():
    sid = voice_trace.start_session()
    tool_id = voice_trace.open_tool(sid, "invoke_deep_agent", {})["tool_id"]
    assert voice_trace.close_tool(sid, tool_id, {"answer": "three SKUs"}) is True
    span = _root(sid).children[0]
    assert span.outputs == {"answer": "three SKUs"} and span.patched
    # A replayed close (double-send from the browser) is a no-op, not a second patch.
    assert voice_trace.close_tool(sid, tool_id, {"answer": "again"}) is False
    assert span.outputs == {"answer": "three SKUs"}


def test_ending_a_session_closes_a_tool_span_left_running():
    """A tab closed mid-run must not leave a span spinning forever in the trace."""
    sid = voice_trace.start_session()
    voice_trace.open_tool(sid, "invoke_deep_agent", {})
    root = _root(sid)
    assert voice_trace.end_session(sid, {"turns": 1}) is True
    assert root.children[0].outputs == {"status": "abandoned"}
    assert root.outputs == {"turns": 1} and root.patched
    # And the session is gone, so a second end is a no-op.
    assert voice_trace.end_session(sid) is False


def test_sessions_are_capped_so_abandoned_tabs_cannot_grow_the_table():
    ids = [voice_trace.start_session() for _ in range(voice_trace._MAX_SESSIONS + 5)]
    assert len(voice_trace._SESSIONS) == voice_trace._MAX_SESSIONS
    # Oldest evicted, newest kept: the live conversation is always the newest.
    assert ids[-1] in voice_trace._SESSIONS
    assert ids[0] not in voice_trace._SESSIONS


def test_ending_a_session_attaches_the_conversation_audio():
    """The attachment is what makes a voice trace playable, so its shape matters.

    LangSmith renders a `(mime, bytes)` attachment on an `ls_modality: audio` run as a
    scrubbable player. A wrong mime or a bare bytes value is not an error anywhere - the
    run just quietly has no audio.
    """
    sid = voice_trace.start_session()
    assert voice_trace.end_session(sid, {"turns": 2}, b"RIFFfake-wav-bytes")
    root = _root(sid) if sid in voice_trace._SESSIONS else None
    assert root is None, "the session is closed and removed"


def test_the_attachment_carries_the_wav_mime_and_bytes(monkeypatch):
    sid = voice_trace.start_session()
    run = voice_trace._SESSIONS[sid]["run"]
    voice_trace.end_session(sid, {}, b"RIFF....")
    assert run.attachments == {"conversation": ("audio/wav", b"RIFF....")}


def test_no_audio_means_no_attachment():
    """A conversation with nothing captured must not attach an empty file."""
    sid = voice_trace.start_session()
    run = voice_trace._SESSIONS[sid]["run"]
    voice_trace.end_session(sid, {}, b"")
    assert not hasattr(run, "attachments") or not run.attachments
