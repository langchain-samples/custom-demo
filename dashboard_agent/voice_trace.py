"""One LangSmith trace per voice conversation.

Without this, voice mode produces two disconnected pictures: the agent runs trace
normally (they are ordinary runs), and everything the user and the model actually SAID
is invisible. With it, a conversation is a single tree:

    voice_session                  <- one per connection
      |- user_speech / agent_speech  <- transcript utterances
      `- invoke_deep_agent (tool)    <- the long-running call
           `- the agent run          <- nests via distributed tracing

The nesting is the load-bearing part and it is NOT automatic. The agent run is started
by the BROWSER (that is what keeps the dashboard alive - see voice.ts), so it cannot
inherit a Python tracing context. Instead `open_tool` hands back the
`langsmith-trace`/`baggage` headers for the tool span; the SPA sends them on the run
request, Agent Server surfaces them as configurable values, and graph.py wraps the run
in `tracing_context(parent=...)`. Both ends have to opt in, which is why this module and
that factory have to agree.

Shape borrowed from langchain-ai/google-adk-realtime-deepagents-example (app/tracing.py),
which does the same thing for a server-side voice loop.

SPIKE SHAPE: sessions live in a module-level dict, so they are per-process and lost on
restart - the same trade-off `webapp._INFLIGHT` already makes. Fine for a demo where a
session is one browser tab for a few minutes; not a durable store.
"""

from __future__ import annotations

import os
import threading
import traceback
import uuid

from langsmith import Client, RunTree

from .config import load_env

# session_id -> the conversation's root span, plus its open tool spans by id.
_SESSIONS: dict[str, dict] = {}
_LOCK = threading.Lock()

# A cap, so a browser that opens sessions and never closes them cannot grow this
# dict without bound. Oldest-first eviction: a leaked session is dead weight, and the
# live one is always the newest.
_MAX_SESSIONS = 32


def _client(workspace: str = "") -> Client | None:
    """LangSmith client for the assistant's workspace, or None when unusable.

    Same key precedence as graph.py's trace routing: an org-scoped cross-workspace key
    if there is one, else the default. None means "do not trace", never an exception -
    a conversation must not fail because its trace could not be opened.
    """
    load_env()
    key = os.getenv("LS_CROSS_WORKSPACE_KEY") or os.getenv("LANGSMITH_API_KEY")
    if not key:
        return None
    api_url = os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
    return Client(api_key=key, api_url=api_url, workspace_id=workspace or None)


def start_session(workspace: str = "", project: str = "", metadata: dict | None = None) -> str:
    """Open the conversation's root span. Returns a session id ("" when not tracing).

    `ls_modality: audio` in the metadata is what makes LangSmith render the run with
    its audio affordances, and matches what the ADK example sets.
    """
    client = _client(workspace)
    if client is None:
        return ""
    # `ls_client`, not `client`: the latter is not a field on the model, so Pydantic
    # silently drops it and the run posts with the default (wrong-workspace) client.
    # `project_name` is typed as a plain str, so an unset project omits the kwarg
    # rather than passing None.
    kwargs: dict = {"project_name": project} if project else {}
    run = RunTree(
        name="voice_session",
        run_type="chain",
        inputs={},
        ls_client=client,
        tags=["voice-mode"],
        extra={"metadata": {"ls_modality": "audio", **(metadata or {})}},
        **kwargs,
    )
    run.post()
    session_id = str(uuid.uuid4())
    with _LOCK:
        _SESSIONS[session_id] = {"run": run, "tools": {}}
        while len(_SESSIONS) > _MAX_SESSIONS:
            _SESSIONS.pop(next(iter(_SESSIONS)))
    return session_id


def _session(session_id: str) -> dict | None:
    with _LOCK:
        return _SESSIONS.get(session_id)


def utterance(session_id: str, role: str, text: str) -> bool:
    """Record one side of the conversation as a leaf span. False when there is no session.

    Transcripts, not audio: a native-audio session returns no text of its own, so these
    ARE the record of what was said (the SPA gets them from Live's input/output
    transcription and forwards them here).
    """
    session = _session(session_id)
    if session is None or not text.strip():
        return False
    name = "user_speech" if role == "user" else "agent_speech"
    child = session["run"].create_child(name=name, run_type="chain", inputs={"text": text})
    child.post()
    child.end(outputs={"text": text})
    child.patch()
    return True


def open_tool(session_id: str, name: str, inputs: dict) -> dict:
    """Open a tool span and return `{tool_id, headers}` for the agent run to nest under.

    `headers` is `RunTree.to_headers()` - `langsmith-trace` plus `baggage`. The SPA
    passes them straight through on the run request; graph.py turns them back into a
    tracing parent. Returns `{}` when there is no session, which the caller treats as
    "trace nothing, run normally".
    """
    session = _session(session_id)
    if session is None:
        return {}
    child = session["run"].create_child(name=name, run_type="tool", inputs=inputs)
    child.post()
    tool_id = str(uuid.uuid4())
    session["tools"][tool_id] = child
    return {"tool_id": tool_id, "headers": dict(child.to_headers())}


def close_tool(session_id: str, tool_id: str, outputs: dict) -> bool:
    """Close a tool span opened by `open_tool`. False when it is unknown (or replayed).

    An `outputs["run_id"]` is turned into the agent run's URL, because that link is the
    whole substitute for nesting: the agent run cannot be a child of this span (see the
    note in graph.py), so the span has to say where the run is instead. Best-effort - the
    id alone is still useful, and the run may not have finished ingesting yet.
    """
    session = _session(session_id)
    if session is None:
        return False
    child = session["tools"].pop(tool_id, None)
    if child is None:
        return False
    run_id = str(outputs.get("run_id") or "")
    if run_id:
        try:
            url = getattr(session["run"].ls_client.read_run(run_id), "url", "")
            if url:
                outputs = {**outputs, "agent_trace": url}
        except Exception:  # noqa: BLE001 - the id on its own is enough to find the run
            pass
    child.end(outputs=outputs)
    child.patch()
    return True


def end_session(session_id: str, outputs: dict | None = None, audio_wav: bytes = b"") -> bool:
    """Close the root span, attaching the conversation audio when there is any.

    The attachment is what turns the trace into something you can LISTEN to: LangSmith
    renders a `(mime, bytes)` attachment on a run tagged `ls_modality: audio` as a scrubbable
    player, with the user on the left channel and the assistant on the right (the browser
    builds the stereo mix - see `lib/voiceRecorder.ts`). Same shape as the ADK example's
    server-side recorder.

    Also closes any tool span still open, so a tab shut mid-run does not leave one spinning.
    """
    with _LOCK:
        session = _SESSIONS.pop(session_id, None)
    if session is None:
        return False
    for child in session["tools"].values():
        child.end(outputs={"status": "abandoned"})
        child.patch()
    run = session["run"]
    if audio_wav:
        # Best-effort: a conversation that happened is worth recording even if its audio
        # cannot be stored (a big body, a rejected upload).
        try:
            run.attachments = {"conversation": ("audio/wav", audio_wav)}
        except Exception:  # noqa: BLE001
            traceback.print_exc()
    run.end(outputs=outputs or {})
    run.patch()
    return True
