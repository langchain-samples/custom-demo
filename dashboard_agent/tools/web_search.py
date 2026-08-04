"""Real web search, backed by the Tavily API.

This tool used to live in `simulated.py`, where a fast LLM invented plausible
results — believable outlets, believable URLs, believable dates. That is fine for
the other simulated capabilities, but the agent is told to *cite* what search
returns, so invented URLs reached the user as citations. Hence a real API.

Deliberately no offline fallback: without `TAVILY_API_KEY` the tool returns an
error object. Failing visibly is the point — a silent fall back to simulation
would reintroduce exactly the fabrication this module exists to remove.

Returns the same JSON shape the simulated version did,
`{"results":[{title,url,snippet,published}]}`, so the frontend's typed card
(`ToolResultCard.tsx` → `SearchCard`) renders it unchanged.
"""

from __future__ import annotations

import json
from typing import Any

from langchain.tools import tool

from ..config import require_tavily_key

# How many results to request. Matches what the simulated tool used to return,
# which is what the card was laid out for.
_MAX_RESULTS = 4

_CLIENT: Any = None


def _client() -> Any:
    """Build (once) the Tavily search client.

    Cached module-level like `simulated._model`, so the key lookup and client
    construction happen on first search rather than at import time — importing
    the tool catalogue must not require a Tavily key.
    """
    global _CLIENT
    if _CLIENT is None:
        from langchain_tavily import TavilySearch
        from langchain_tavily._utilities import TavilySearchAPIWrapper

        # Raises when unset; callers below turn that into an error payload.
        key = require_tavily_key()
        _CLIENT = TavilySearch(
            max_results=_MAX_RESULTS,
            api_wrapper=TavilySearchAPIWrapper(tavily_api_key=key),
        )
    return _CLIENT


def _shape(raw: dict) -> str:
    """Map a Tavily response onto the card's `{results:[...]}` contract."""
    results = []
    for hit in raw.get("results") or []:
        if not isinstance(hit, dict):
            continue
        results.append(
            {
                "title": hit.get("title") or "",
                "url": hit.get("url") or "",
                "snippet": hit.get("content") or "",
                # Only present for news/finance topics; the card omits the line
                # when it's empty, so "" is a fine general-search answer.
                "published": hit.get("published_date") or "",
            }
        )
    return json.dumps({"results": results}, ensure_ascii=False)


@tool
def web_search(query: str) -> str:
    """Search the web for external context and citable sources.

    Use for context that would not appear in internal data — market conditions,
    competitors, regulation, public news. Cite what you use in your answer.

    Returns JSON {results:[{title, url, snippet, published}]}.
    """
    try:
        raw = _client().invoke({"query": query})
        # TavilySearch swallows request failures into {"error": ...} instead of
        # raising, so an error can arrive as a perfectly ordinary return value.
        if isinstance(raw, dict) and raw.get("error"):
            return json.dumps({"error": f"web search failed: {raw['error']}"})
        if not isinstance(raw, dict):
            return json.dumps({"error": "web search returned an unexpected response"})
        return _shape(raw)
    except Exception as exc:
        # Never raise: a search outage should degrade this one card, not kill the
        # run — same convention as `simulated.simulate`. Covers the missing-key
        # RuntimeError and the ToolException raised on zero results.
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})
