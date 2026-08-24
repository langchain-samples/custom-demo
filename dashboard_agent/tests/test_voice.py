"""Tests for the voice shell's token route (dashboard_agent/voice.py).

Offline: `token_request` is pure, and the one HTTP call is driven through a stub. What
these guard is the PINNING - a token that is not constrained to one model and one
modality is a browser-reachable credential that can do more than talk to our shell.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dashboard_agent import voice


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    """Neutral env: each test says explicitly whether a key exists."""
    monkeypatch.setattr(voice, "load_env", lambda: None)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def test_token_request_pins_the_model_and_modality():
    """Wire names, not the guide's SDK names.

    `liveConnectConstraints` / `lockAdditionalFields` read naturally and are what the
    guide shows, but the REST resource answers `Unknown name "liveConnectConstraints" at
    'auth_token'` - which is a 400 nobody sees until someone tries to talk. Verified
    against the v1beta discovery document's `AuthToken`.
    """
    body = voice.token_request("gemini-2.5-flash-native-audio-preview-12-2025")
    assert "liveConnectConstraints" not in body and "lockAdditionalFields" not in body
    setup = body["bidiGenerateContentSetup"]
    # `models/` prefix: the Live API's model field is a resource name, not a bare id.
    assert setup["model"] == "models/gemini-2.5-flash-native-audio-preview-12-2025"
    # Modality lives under generationConfig on the wire, not beside the model.
    assert setup["generationConfig"]["responseModalities"] == ["AUDIO"]
    # Single use, so a leaked token buys one session at most.
    assert body["uses"] == 1
    # PRESENT and empty: an empty field mask is what makes the pinning binding.
    assert body["fieldMask"] == ""


def test_token_request_enables_resumption_and_both_transcripts():
    cfg = voice.token_request("m")["bidiGenerateContentSetup"]
    # A Live connection dies at ~10 minutes; a demo does not. Resumption has to be
    # pinned into the token's config or a reconnect is refused.
    assert "sessionResumption" in cfg
    # A native-audio model returns no text, so the chat transcript IS these.
    assert "inputAudioTranscription" in cfg
    assert "outputAudioTranscription" in cfg


def test_voice_is_unconfigured_without_a_key(monkeypatch):
    assert voice.voice_configured() is False
    monkeypatch.setenv("GEMINI_API_KEY", "  ")
    assert voice.voice_configured() is False, "whitespace is not a key"
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert voice.voice_configured() is True


def test_mint_token_refuses_without_a_key():
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        voice.mint_token()


def test_mint_token_returns_the_resource_name_as_the_token(monkeypatch):
    """The token the browser uses IS the resource `name`, not an `id` or a `token`."""
    # A distinctive value, so the "never in the URL" assertion below cannot pass by
    # accident (a short one like "k" is a substring of the endpoint path itself).
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key-9f3a")
    sink: dict = {}

    def _post(url, headers=None, json=None, timeout=None):  # noqa: A002
        sink.update({"url": url, "headers": headers, "json": json})
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"name": "auth_tokens/abc", "expireTime": "2026-08-24T12:00:00Z"},
        )

    monkeypatch.setattr(voice.httpx, "post", _post)
    out = voice.mint_token("m-1")

    assert out == {"token": "auth_tokens/abc", "model": "m-1", "expires_at": "2026-08-24T12:00:00Z"}
    # The key travels in the header Google documents, and never in the URL (where it
    # would land in logs and referrers).
    assert sink["headers"]["x-goog-api-key"] == "not-a-real-key-9f3a"
    assert "not-a-real-key-9f3a" not in sink["url"]


def test_mint_token_raises_on_a_200_with_no_name(monkeypatch):
    """A token we cannot use is a failure, not a value to hand the browser."""
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(
        voice.httpx,
        "post",
        lambda *_a, **_k: SimpleNamespace(raise_for_status=lambda: None, json=lambda: {}),
    )
    with pytest.raises(RuntimeError, match="no name"):
        voice.mint_token()


def test_the_default_model_is_a_live_model():
    """A bump to a non-Live model would fail only at connect time, in front of an audience.

    Deliberately loose about WHICH Live model: 3.1 and the 2.5 native-audio one both work,
    because the two-phase tool response (voice.ts) is what keeps the model talking during a
    long run, not the 2.5-only `NON_BLOCKING` flag. See `config.voice_model`.
    """
    from dashboard_agent.config import voice_model

    model = voice_model()
    assert model.startswith("gemini-")
    assert "live" in model or "native-audio" in model
