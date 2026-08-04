"""Deterministic tests for the Tavily-backed web_search tool (no network, no key).

The Tavily client is monkeypatched, so these exercise the real tool body: the
mapping onto the card's `{results:[...]}` contract and — the point of the tool —
that every failure path yields an error payload rather than invented results.
"""

from __future__ import annotations

import json

import pytest

from dashboard_agent import config
from dashboard_agent.tools import web_search as ws


@pytest.fixture(autouse=True)
def _no_key_no_cache(monkeypatch):
    """Start every test keyless with a cold client cache.

    Two things have to be neutralised, not one. Unsetting the variable is not
    enough: `require_tavily_key` calls `load_env()`, which would read a real key
    straight back out of the developer's `.env` and turn the missing-key test
    green for the wrong reason. So stub the loader too.

    The client cache is module-level, so without resetting it a client built by
    one test would leak into the next and mask the missing-key path.
    """
    monkeypatch.setattr(config, "load_env", lambda: None)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setattr(ws, "_CLIENT", None)


def _stub(monkeypatch, result):
    """Install a fake Tavily client returning (or raising) `result`."""

    class _Fake:
        def invoke(self, _payload):
            if isinstance(result, Exception):
                raise result
            return result

    monkeypatch.setattr(ws, "_client", lambda: _Fake())


def test_maps_tavily_hits_onto_card_contract(monkeypatch):
    _stub(
        monkeypatch,
        {
            "results": [
                {
                    "title": "EU AI Act enforcement begins",
                    "url": "https://example.com/ai-act",
                    "content": "Obligations take effect in August.",
                    "published_date": "2026-07-02",
                    "score": 0.98,  # extra Tavily fields are dropped
                }
            ]
        },
    )
    out = json.loads(ws.web_search.invoke({"query": "EU AI Act"}))
    assert out["results"] == [
        {
            "title": "EU AI Act enforcement begins",
            "url": "https://example.com/ai-act",
            "snippet": "Obligations take effect in August.",  # content -> snippet
            "published": "2026-07-02",
        }
    ]


def test_missing_published_date_becomes_empty_string(monkeypatch):
    # General-topic searches carry no date; the card omits the line when empty.
    _stub(monkeypatch, {"results": [{"title": "T", "url": "https://e.com", "content": "c"}]})
    out = json.loads(ws.web_search.invoke({"query": "q"}))
    assert out["results"][0]["published"] == ""


def test_missing_api_key_errors_and_never_fabricates(monkeypatch):
    # No _client stub: the real one runs and require_tavily_key() raises.
    out = json.loads(ws.web_search.invoke({"query": "q"}))
    assert "TAVILY_API_KEY" in out["error"]
    assert "results" not in out


def test_client_exception_becomes_error_payload(monkeypatch):
    _stub(monkeypatch, RuntimeError("connection reset"))
    out = json.loads(ws.web_search.invoke({"query": "q"}))
    assert out["error"] == "RuntimeError: connection reset"
    assert "results" not in out


def test_error_dict_return_is_not_treated_as_results(monkeypatch):
    # TavilySearch returns {"error": ...} instead of raising on request failure.
    _stub(monkeypatch, {"error": "401 Unauthorized"})
    out = json.loads(ws.web_search.invoke({"query": "q"}))
    assert "401 Unauthorized" in out["error"]
    assert "results" not in out
