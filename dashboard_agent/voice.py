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

_TOKENS_URL = "https://generativelanguage.googleapis.com/v1beta/auth_tokens"

# Audio only. A native-audio model accepts ONLY this modality, so text for the chat
# transcript comes from output transcription rather than a TEXT response.
_MODALITIES = ["AUDIO"]


def voice_configured() -> bool:
    """Is a Gemini key present? Voice mode is unavailable without one."""
    load_env()
    return bool(os.getenv("GEMINI_API_KEY", "").strip())


def token_request(model: str) -> dict:
    """The `POST /v1beta/auth_tokens` body. Pure, so the pinning is testable.

    Field names come from the REST resource (the v1beta discovery document's `AuthToken`),
    NOT from the guide: the guide shows the SDK's `liveConnectConstraints` /
    `lockAdditionalFields`, and posting those verbatim is rejected with
    `Unknown name "liveConnectConstraints" at 'auth_token'`. The wire names are
    `bidiGenerateContentSetup` and `fieldMask`, and the pinned config is the Live setup
    message itself rather than a nested `config` object.

    `sessionResumption` is pinned because it is not optional in practice: a Live
    connection dies at ~10 minutes and an audio session caps at 15, so a demo that
    outlasts either has to reconnect on the same token.
    """
    return {
        "uses": 1,
        "bidiGenerateContentSetup": {
            "model": f"models/{model}",
            "generationConfig": {"responseModalities": _MODALITIES},
            "sessionResumption": {},
            # Both directions: the input transcript is what the chat panel shows as the
            # user's turn, and the output transcript is the assistant bubble (a
            # native-audio model returns no text of its own).
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
        },
        # Empty field mask = the setup above is locked and the client cannot widen it.
        # Present rather than omitted: this field is what makes the pinning binding.
        "fieldMask": "",
    }


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
    chosen = model or voice_model()
    res = httpx.post(
        _TOKENS_URL,
        headers={"x-goog-api-key": key},
        json=token_request(chosen),
        timeout=15,
    )
    res.raise_for_status()
    body = res.json() or {}
    # The token IS the resource `name` ("auth_tokens/..."), which the client then uses
    # in place of an API key. An empty name means a 200 we cannot use.
    name = str(body.get("name") or "")
    if not name:
        raise RuntimeError(f"token mint returned no name: {str(body)[:200]}")
    return {"token": name, "model": chosen, "expires_at": str(body.get("expireTime") or "")}
