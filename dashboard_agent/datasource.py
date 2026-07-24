"""Pluggable data backend behind the `datasearch` and `query_sql` tools.

Two implementations, selected by `DASHBOARD_DATASET`:

* ``StaticDataSource``    — the bundled humanitarian corpus (default): real
  in-memory TF-IDF + real SQLite. This is the shipped demo.
* ``SyntheticDataSource`` — a live LLM stands in for the backend and invents
  plausible data per call, steered by the data prompt in Prompt Hub
  (`dashboard-agent-data`), which encodes the topic and the planted gap. This is
  what makes the demo domain-agnostic: point it at any topic, no curated corpus.

Both return the same shapes the tools already expect, so `agent.py` doesn't care
which is active. `query_sql` passes the raw SQL string straight through — the
synthetic backend "executes" it by generating plausible rows.
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from .config import data_model, dataset


class DataSource(Protocol):
    def search(self, query: str, k: int = 3) -> list[dict]: ...
    def run_sql(self, query: str) -> dict: ...
    def sql_hint(self) -> str: ...


class StaticDataSource:
    """The bundled humanitarian corpus (default). Real RAG + real SQLite."""

    def search(self, query: str, k: int = 3) -> list[dict]:
        from .rag import search as _search

        return _search(query, k=k)

    def run_sql(self, query: str) -> dict:
        from .database import run_query

        return run_query(query)

    def sql_hint(self) -> str:
        from .database import SCHEMA_DESCRIPTION

        return (
            SCHEMA_DESCRIPTION
            + "\n\nExample: SELECT sector, funding_usd FROM egypt_sector_funding ORDER BY funding_usd DESC"
        )


_JSON_RE = re.compile(r"[\[{].*[\]}]", re.S)


def _parse_json(text: str) -> Any:
    """Best-effort parse of a model reply that should be JSON."""
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        m = _JSON_RE.search(text)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
        return None


class SyntheticDataSource:
    """A live LLM invents plausible data per call, steered by the data prompt.

    The topic and the withheld "gap" live in the data prompt (Prompt Hub), so the
    presenter controls the world without a redeploy. `model` and `data_prompt_name`
    can be set per assistant (via runtime context) or fall back to config.
    """

    def __init__(self, model: str | None = None, data_prompt_name: str | None = None) -> None:
        self._model_id = model or data_model()
        self._data_prompt_name = data_prompt_name
        self._llm = None

    def _model(self):
        if self._llm is None:
            from langchain.chat_models import init_chat_model

            # Low-ish temperature: plausible but not wildly random figures.
            self._llm = init_chat_model(self._model_id, temperature=0.4)
        return self._llm

    def _ask(self, instruction: str) -> str:
        from langchain_core.messages import HumanMessage, SystemMessage

        from .prompt import pull_data_prompt

        messages = [SystemMessage(pull_data_prompt(self._data_prompt_name)), HumanMessage(instruction)]
        resp = self._model().invoke(messages)
        content = getattr(resp, "content", "")
        if isinstance(content, list):  # some providers return content blocks
            content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
        return content or ""

    def search(self, query: str, k: int = 3) -> list[dict]:
        out = self._ask(
            f"A dashboard agent called `datasearch` with query: {query!r}.\n"
            f"Return up to {k} matching documents as STRICT JSON, no prose:\n"
            '{"results":[{"title":"","source":"","region":"","period":"","text":"","data":{}}]}\n'
            "`text` is prose to ground an answer; `data` is a small object of numeric "
            "figures to chart. Honor the withheld topics in your instructions — "
            "return an empty results list for those."
        )
        parsed = _parse_json(out)
        results = parsed.get("results") if isinstance(parsed, dict) else parsed
        return results if isinstance(results, list) else []

    def run_sql(self, query: str) -> dict:
        out = self._ask(
            "A dashboard agent called `query_sql` with the SQL below. Act as the "
            "database: execute it against the dataset you represent and return STRICT "
            "JSON, no prose:\n"
            '{"columns":["col"],"rows":[["value"]],"row_count":1}\n'
            "If the SQL targets withheld or nonexistent data, return "
            '{"columns":[],"rows":[],"row_count":0}.\n\nSQL:\n' + query
        )
        parsed = _parse_json(out)
        if isinstance(parsed, dict) and "columns" in parsed and "rows" in parsed:
            parsed.setdefault("row_count", len(parsed.get("rows") or []))
            return parsed
        return {"columns": [], "rows": [], "row_count": 0, "error": "no data available"}

    def sql_hint(self) -> str:
        return (
            "The database schema is inferred from the dataset topic. Write standard "
            "read-only SQL SELECTs with plausible table/column names; queries for "
            "withheld or unknown data return zero rows."
        )


_CACHE: dict = {}


def get_datasource(
    dataset_name: str | None = None,
    model: str | None = None,
    data_prompt_name: str | None = None,
) -> DataSource:
    """Return the active data source, chosen by args (assistant context) or env.

    Cached per (dataset, model, data_prompt) so multiple assistants in one process
    each get a stable backend without rebuilding the model every tool call.
    """
    name = (dataset_name or dataset()).strip().lower()
    if name.startswith("synthetic"):
        key = ("synthetic", model, data_prompt_name)
        if key not in _CACHE:
            _CACHE[key] = SyntheticDataSource(model=model, data_prompt_name=data_prompt_name)
        return _CACHE[key]
    if "static" not in _CACHE:
        _CACHE["static"] = StaticDataSource()
    return _CACHE["static"]
