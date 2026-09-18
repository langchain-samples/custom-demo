"""Voice mode's two routes: minting a Live API token, and tracing the conversation.

The audio never touches this deployment — the browser talks to Google directly —
so all that is left here is a credential the SPA must not be able to read off a
bundle, and the bookkeeping that turns a spoken conversation into one LangSmith
trace. The work itself lives in `custom_demo/voice/`.
"""

from __future__ import annotations

import base64
import traceback

from starlette.responses import JSONResponse

from custom_demo.voice import mint_token, voice_configured
from custom_demo.voice import trace as voice_trace_mod


async def voice_token(request):
    """Mint a short-lived Gemini Live token for the voice shell.

    POST -> {token, model, expires_at}. The browser connects to Google directly with
    this instead of an API key, so `GEMINI_API_KEY` stays server-side and no audio ever
    transits this deployment. See voice.py for what the token is pinned to.

    Deliberately a POST with no body: it mints a credential, so it must not be
    cacheable or reachable by a stray link, and it inherits whatever auth the
    deployment enforces on this app (see auth.py).
    """
    if not voice_configured():
        return JSONResponse(
            {"error": "voice mode is unavailable: GEMINI_API_KEY is not set"}, status_code=501
        )

    try:
        return JSONResponse(mint_token())
    except Exception as exc:  # noqa: BLE001 - see below - no degraded mode; tell the client the mint failed
        # No degraded mode worth having: without a token the client cannot connect, so
        # say so rather than handing back something it will fail on.
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=502)


def _closing_audio(body: dict) -> bytes:
    """The closing call's base64 conversation audio, or empty bytes.

    A few MB for a long session, so the trace gets a playable timeline. Decoded
    defensively: a malformed blob must not cost the session its closing patch.
    """
    raw = str(body.get("audio_wav") or "")
    if not raw:
        return b""

    try:
        return base64.b64decode(raw)
    except Exception:  # noqa: BLE001
        return b""


def _session_metadata(body: dict) -> dict:
    """The `session` action's metadata, checked for the keys that make a trace attributable.

    `assistant_id`, `agent_repo`, `graph_id` and `user_id` are what let a voice trace be
    filtered to one assistant, one prompt source and one user, exactly as an agent run
    already can be; the SPA reads them off the presenter session and sends them here.
    A session missing them still opens, because a conversation must not fail over its
    bookkeeping, but the root run names what never arrived under `identity_missing` so an
    unattributable trace says why it is unattributable.
    """
    metadata = {k: v for k, v in (body.get("metadata") or {}).items() if v not in (None, "")}
    missing = [key for key in voice_trace_mod.IDENTITY_KEYS if key not in metadata]
    if missing:
        metadata["identity_missing"] = missing

    return metadata


def _voice_action(action: str, body: dict) -> JSONResponse:
    """Dispatch one bookkeeping call. See `voice_trace` for the five shapes."""
    if action == "session":
        return JSONResponse(
            {
                "session_id": voice_trace_mod.start_session(
                    str(body.get("workspace") or ""),
                    str(body.get("project") or ""),
                    _session_metadata(body),
                )
            }
        )

    session_id = str(body.get("session_id") or "")
    if action == "utterance":
        ok = voice_trace_mod.utterance(
            session_id, str(body.get("role") or ""), str(body.get("text") or "")
        )
        return JSONResponse({"ok": ok})

    if action == "tool":
        return JSONResponse(
            voice_trace_mod.open_tool(
                session_id, str(body.get("name") or "tool"), body.get("inputs") or {}
            )
        )

    if action == "tool_end":
        ok = voice_trace_mod.close_tool(
            session_id, str(body.get("tool_id") or ""), body.get("outputs") or {}
        )
        return JSONResponse({"ok": ok})

    if action == "end":
        ok = voice_trace_mod.end_session(
            session_id, body.get("outputs") or {}, _closing_audio(body)
        )
        return JSONResponse({"ok": ok})

    return JSONResponse({"error": f"unknown action {action!r}"}, status_code=400)


async def voice_trace(request):
    """Record the voice conversation into ONE LangSmith trace. See voice/trace.py.

    POST {action, ...} -> the shape depends on the action, because all four are the same
    small bookkeeping call and four routes for them would be noise:

      session   {workspace?, project?, metadata?}      -> {session_id}
      utterance {session_id, role, text}               -> {ok}
      tool      {session_id, name, inputs}             -> {tool_id, headers}
      tool_end  {session_id, tool_id, outputs}         -> {ok}
      end       {session_id, outputs?}                 -> {ok}

    `tool` is the interesting one: its `headers` are what the SPA puts on the agent run
    so the run nests under the tool span instead of starting its own trace.

    `session`'s `metadata` carries the conversation's identity - `thread_id`, `customer`
    and the four keys in `_session_metadata` - and an `end` with no `outputs` is the normal
    case: the root is closed with the transcript summary voice/trace.py keeps.

    Best-effort throughout: a conversation must not break because its trace could not be
    written, so a missing session or an unusable LangSmith key answers `{}` / `ok: false`
    rather than an error the UI has to handle.
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - an unreadable body is a client error; answer 400, not 500
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    action = str(body.get("action") or "")
    try:
        return _voice_action(action, body)
    except Exception as exc:  # noqa: BLE001 - a corrupt base64 blob loses the audio, not the span
        # Logged, not raised: losing a span is not worth ending a conversation over.
        traceback.print_exc()
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=200)
