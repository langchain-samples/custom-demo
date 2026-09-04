"""Ephemeral-token minting for the voice shell (the Gemini Live half of voice mode).

WHAT VOICE MODE IS. Gemini's Live API runs the CONVERSATION - microphone, voice
activity detection, barge-in, speech out - and gets exactly one capability: a
`invoke_deep_agent` function call. The browser implements that call by starting a
normal streaming run against this deployment, so the dashboard, the chat transcript,
the subagent cards and the LangSmith trace are produced by the same code path as a
typed question. Nothing about the agent, its tools or its prompt is voice-aware. See
`frontend/src/lib/voice.ts` for the other half.

WHY A TOKEN ROUTE EXISTS. The browser connects to Google DIRECTLY (Google documents
that as the lower-latency shape, and it keeps audio off this deployment entirely, so
there is no WebSocket to proxy). A browser cannot hold `GEMINI_API_KEY`, so it gets an
EPHEMERAL token instead: minted here, short-lived, single-use, and pinned by
`liveConnectConstraints` to one model and one response modality. `lockAdditionalFields`
is sent empty, which is what makes the pinned fields un-overridable by the client.

Defaults worth knowing (Google's, not ours): a token's `newSessionExpireTime` is ~60s
(the window to OPEN a session), `expireTime` ~30min (the window to keep talking), and
`uses` is 1. Resuming a dropped session does not spend a use, which matters because a
Live connection lasts only ~10 minutes and a demo does not.
"""

from __future__ import annotations

import os

import httpx

from .config import load_env, voice_model

# Google directly, NOT the LangSmith gateway - unlike every other model call in this
# repo, which moved to `langsmith:` model ids. Checked 2026-09-04, three reasons:
#   1. The gateway allow-lists paths, and this is not one:
#      POST /gemini/v1beta/auth_tokens -> 501 "path not allow-listed by gateway".
#   2. Live is a WebSocket protocol; the gateway is request/response over HTTPS.
#      The browser dials wss://generativelanguage.googleapis.com itself (lib/voice.ts).
#   3. The gateway's Gemini catalog has no interactive Live model - one transcribe-only
#      entry, not the native-audio dialog model below.
# So GEMINI_API_KEY stays required for voice even when everything else runs on gateway
# credits. Ephemeral minting is what keeps that key server-side; do not hand the raw key
# to the browser as a shortcut.
_TOKENS_URL = "https://generativelanguage.googleapis.com/v1beta/auth_tokens"

# Audio only. A native-audio model accepts ONLY this modality, so text for the chat
# transcript comes from output transcription rather than a TEXT response.
_MODALITIES = ["AUDIO"]


def voice_configured() -> bool:
    """Is a Gemini key present? Voice mode is unavailable without one."""
    load_env()
    return bool(os.getenv("GEMINI_API_KEY", "").strip())


def token_request(model: str) -> dict:
    """The `POST /v1beta/auth_tokens` body. Pure, so what is (and is not) pinned is testable.

    Deliberately minimal, and that took a live session to establish. Field names here come
    from the REST resource (the v1beta discovery document's `AuthToken`), NOT the guide:
    the guide's `liveConnectConstraints` / `lockAdditionalFields` are SDK names and are
    rejected with `Unknown name "liveConnectConstraints" at 'auth_token'`.

    WHY NOTHING IS PINNED. The tempting shape is to pin the whole session
    (`bidiGenerateContentSetup` + an empty `fieldMask`), which per the discovery document
    makes the token's setup authoritative and the client's "ignored" - the browser could
    then not widen its own capabilities. Verified against the live API: it does not work.
    A pinned token closes the socket with `1011 Internal error encountered`, and a client
    that sends `setup: {}` to lean on the pinned config gets `1007 token-based requests
    cannot use project-scoped features such as tuned models`. An unpinned token with a
    client-supplied setup reaches `setupComplete` on the first try.

    So the session config (model, tools, instructions) lives in `frontend/src/lib/voice.ts`
    and this token is only a short-lived, SINGLE-USE ticket. What that costs: a client
    could open a session configured differently from ours. What bounds it: `uses: 1`, a
    ~60 second window to open the session, and the fact that the token cannot do anything
    outside the Live API.

    `model` is accepted (and ignored) so callers can keep passing the model they intend to
    connect with; it is the client's setup that selects it.
    """
    return {"uses": 1}


def mint_token(model: str = "") -> dict:
    """Mint an ephemeral Live API token. Returns `{token, model, expires_at}`.

    Raises on a missing key or a failed mint - unlike most of this app's LangSmith
    calls, there is no useful degraded mode: without a token the browser has nothing
    to connect with, and the caller turns that into a 4xx/5xx the UI can report.
    """
    load_env()
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set, so voice mode cannot mint a token")
    # A Google API key starts "AIza". Checked because the failure it prevents is very
    # expensive to read: a wrong-but-present key mints nothing and the UI just says
    # "Connecting..." and drops back, with the real reason (a 400 whose body we used to
    # discard) nowhere on screen. It happened for real - this machine's shell exported a
    # stale GEMINI_API_KEY holding a LangSmith key, and since load_dotenv does not
    # override an existing variable, .env's correct key never won.
    if not key.startswith("AIza"):
        raise RuntimeError(
            "GEMINI_API_KEY does not look like a Google API key (expected it to start "
            f"with 'AIza', got '{key[:8]}...'). A shell variable of the same name takes "
            "precedence over .env, so check the environment as well as the file."
        )
    chosen = model or voice_model()
    res = httpx.post(
        _TOKENS_URL,
        headers={"x-goog-api-key": key},
        json=token_request(chosen),
        timeout=15,
    )
    if res.status_code >= 400:
        # Carry Google's own message. raise_for_status alone gives a bare status and a
        # link, which is what made this take an hour to place.
        raise RuntimeError(f"token mint failed (HTTP {res.status_code}): {res.text[:300]}")
    body = res.json() or {}
    # The token IS the resource `name` ("auth_tokens/..."), which the client then uses
    # in place of an API key. An empty name means a 200 we cannot use.
    name = str(body.get("name") or "")
    if not name:
        raise RuntimeError(f"token mint returned no name: {str(body)[:200]}")
    return {"token": name, "model": chosen, "expires_at": str(body.get("expireTime") or "")}
