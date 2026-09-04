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


def test_token_request_pins_nothing_but_the_use_count():
    """What is ABSENT here is the point, and it cost a live session to learn.

    Pinning the session into the token (`bidiGenerateContentSetup` + an empty `fieldMask`)
    is what the docs invite, and it fails: the socket closes with `1011 Internal error`,
    and a client that then sends `setup: {}` gets `1007 token-based requests cannot use
    project-scoped features`. An unpinned token reaches `setupComplete`. So the guard is
    that nobody re-adds the pinning after reading the guide.
    """
    body = voice.token_request("gemini-3.1-flash-live-preview")
    assert body == {"uses": 1}
    # Single use, so a leaked token buys one session at most - the only bound left once
    # the config lives client-side.
    assert body["uses"] == 1


def test_token_request_never_sends_the_sdk_field_names():
    """`liveConnectConstraints` / `lockAdditionalFields` are SDK names, not wire names.

    Posting them is rejected with `Unknown name "liveConnectConstraints" at 'auth_token'`,
    which is a 400 nobody sees until someone tries to talk.
    """
    body = voice.token_request("m")
    assert "liveConnectConstraints" not in body
    assert "lockAdditionalFields" not in body


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
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSy-not-a-real-key-9f3a")
    sink: dict = {}

    def _post(url, headers=None, json=None, timeout=None):  # noqa: A002
        sink.update({"url": url, "headers": headers, "json": json})
        return SimpleNamespace(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {"name": "auth_tokens/abc", "expireTime": "2026-08-24T12:00:00Z"},
        )

    monkeypatch.setattr(voice.httpx, "post", _post)
    out = voice.mint_token("m-1")

    assert out == {"token": "auth_tokens/abc", "model": "m-1", "expires_at": "2026-08-24T12:00:00Z"}
    # The key travels in the header Google documents, and never in the URL (where it
    # would land in logs and referrers).
    assert sink["headers"]["x-goog-api-key"] == "AIzaSy-not-a-real-key-9f3a"
    assert "AIzaSy-not-a-real-key-9f3a" not in sink["url"]


def test_mint_token_raises_on_a_200_with_no_name(monkeypatch):
    """A token we cannot use is a failure, not a value to hand the browser."""
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSy-k")
    monkeypatch.setattr(
        voice.httpx,
        "post",
        lambda *_a, **_k: SimpleNamespace(
            status_code=200, raise_for_status=lambda: None, json=lambda: {}
        ),
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


def test_mint_rejects_a_key_that_is_not_a_google_key(monkeypatch):
    """A wrong-but-present key must fail loudly, not as an opaque HTTP 400.

    This is the shape of a real outage: the shell exported a GEMINI_API_KEY holding a
    LangSmith key, load_dotenv left it alone because it was already set, and voice mode
    showed "Connecting..." and dropped back with nothing to go on.
    """
    monkeypatch.setattr(voice, "load_env", lambda: None)
    monkeypatch.setenv("GEMINI_API_KEY", "lsv2_sk_deadbeef")
    with pytest.raises(RuntimeError, match="does not look like a Google API key"):
        voice.mint_token()


def test_mint_surfaces_googles_own_error_text(monkeypatch):
    """The provider's message reaches the caller, instead of a bare status code."""
    monkeypatch.setattr(voice, "load_env", lambda: None)
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSyTestKeyNotReal")

    class Res:
        status_code = 400
        text = '{"error": {"message": "API key not valid."}}'

    monkeypatch.setattr(voice.httpx, "post", lambda *a, **k: Res())
    with pytest.raises(RuntimeError, match="API key not valid"):
        voice.mint_token()
