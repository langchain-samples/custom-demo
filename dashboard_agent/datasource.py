"""Pluggable data backend behind the `datasearch` tool.

Two implementations, selected by `DASHBOARD_DATASET`:

* ``StaticDataSource``    — the bundled humanitarian corpus (default): real
  in-memory TF-IDF search. This is the shipped demo.
* ``SyntheticDataSource`` — a live LLM stands in for the backend and invents
  plausible data per call, steered by the data prompt in Prompt Hub
  (`dashboard-agent-data`), which encodes the topic and the planted gap. This is
  what makes the demo domain-agnostic: point it at any topic, no curated corpus.

Both return the same shape `datasearch` expects, so `agent.py` doesn't care which
is active.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from typing import Any, Protocol

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage

from .config import data_model, dataset
from .prompt import build_data_prompt, build_pii_data_prompt, pull_data_prompt
from .rag import search as _search


class DataSource(Protocol):
    """The interface `datasearch` depends on: a `search(query, k)` returning dicts."""

    def search(self, query: str, k: int = 3) -> list[dict]:
        """Return up to `k` matching records for `query`."""
        ...


class StaticDataSource:
    """The bundled humanitarian corpus (default). Real in-memory TF-IDF search."""

    def search(self, query: str, k: int = 3) -> list[dict]:
        """Return up to `k` TF-IDF matches from the bundled corpus."""
        return _search(query, k=k)


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

    def __init__(
        self,
        model: str | None = None,
        data_prompt_name: str | None = None,
        data_prompt: str | None = None,
        ls_workspace: str | None = None,
        data_gap: str | None = None,
        customer: str | None = None,
        industry: str | None = None,
        pii_focus: str | None = None,
    ) -> None:
        """Store per-assistant data-source config (model, prompt source, planted trigger)."""
        self._model_id = model or data_model()
        self._data_prompt_name = data_prompt_name
        self._data_prompt = data_prompt  # inline text; preferred over everything below
        self._ls_workspace = ls_workspace  # workspace to pull the Hub data prompt from
        self._data_gap = data_gap  # withheld topic → customer-centric prompt built from it
        self._customer = customer
        self._industry = industry
        self._pii_focus = pii_focus  # guaranteed sensitive-field category → PII-leak prompt
        self._llm = None

    def _model(self):
        if self._llm is None:
            # Low-ish temperature: plausible but not wildly random figures.
            self._llm = init_chat_model(self._model_id, temperature=0.4)
        return self._llm

    def _system_prompt(self) -> str:
        # Precedence: inline override → a planted trigger (gap / PII, mutually
        # exclusive per failure mode) → pull a Hub prompt by name → the
        # default. `prompt_injection` mode has no data-source trigger at all —
        # its "attack" is a live user request, not planted document content
        # (see PROMPT_INJECTION_CLAUSE's note on why).
        if self._data_prompt:
            return self._data_prompt
        if self._data_gap:
            return build_data_prompt(self._data_gap, self._customer or "", self._industry or "")
        if self._pii_focus:
            return build_pii_data_prompt(
                self._pii_focus, self._customer or "", self._industry or ""
            )
        return pull_data_prompt(self._data_prompt_name, self._ls_workspace)

    def _ask(self, instruction: str) -> str:
        # Anchor the invented world to today, or the model dates everything to its
        # training cutoff and the dashboard reads as years out of date.
        system = (
            f"{self._system_prompt()}\n\nTODAY'S DATE IS {date.today().isoformat()}. "
            "Every period, date and 'latest' figure you invent must be relative to "
            "that — never an earlier year."
        )
        messages = [SystemMessage(system), HumanMessage(instruction)]
        resp = self._invoke_detached(messages)
        content = getattr(resp, "content", "")
        if isinstance(content, list):  # some providers return content blocks
            content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
        return content or ""

    def _invoke_detached(self, messages):
        """Run the data-simulation LLM as its OWN trace, detached from the agent run.

        In the customer's trace `datasearch` then reads as a plain data lookup, not
        an LLM fabricating data. A worker thread starts with fresh contextvars, so the
        agent run's callbacks aren't inherited (no nesting); we attach our own tracer
        targeting DATA_TRACE_PROJECT. Best-effort: on any setup failure, fall back to a
        normal (nested) invoke so data gen never breaks.
        """
        model = self._model()
        try:
            from concurrent.futures import ThreadPoolExecutor

            from langchain_core.tracers import LangChainTracer

            project = os.getenv("DATA_TRACE_PROJECT", "custom-demos")
            cfg = {
                "callbacks": [LangChainTracer(project_name=project)],
                "run_name": "synthetic-data-source",
            }
            with ThreadPoolExecutor(max_workers=1) as ex:
                return ex.submit(lambda: model.invoke(messages, config=cfg)).result()
        except Exception:
            return model.invoke(messages)

    def search(self, query: str, k: int = 3) -> list[dict]:
        """Ask the LLM for up to `k` synthetic records as JSON; parse to a list."""
        out = self._ask(
            f"A dashboard agent called `datasearch` with query: {query!r}.\n"
            f"Return up to {k} matching documents as STRICT JSON, no prose:\n"
            '{"results":[{"title":"","source":"","region":"","period":"","text":"","data":{}}]}\n'
            "`text` is prose to ground an answer; `data` is a small object of numeric "
            "figures to chart. Return an empty results list ONLY for a topic explicitly marked "
            "WITHHELD in your instructions above. For every other query — including one naming a "
            "specific customer, member, order, or account you have not seen before — invent a "
            "specific, plausible matching record. Never say a record was not found just because "
            "the name or identifier is new to you; that is exactly the kind of lookup this system "
            "of record is for."
        )
        parsed = _parse_json(out)
        results = parsed.get("results") if isinstance(parsed, dict) else parsed
        return results if isinstance(results, list) else []


_CACHE: dict = {}


def get_datasource(
    dataset_name: str | None = None,
    model: str | None = None,
    data_prompt_name: str | None = None,
    data_prompt: str | None = None,
    ls_workspace: str | None = None,
    data_gap: str | None = None,
    customer: str | None = None,
    industry: str | None = None,
    pii_focus: str | None = None,
) -> DataSource:
    """Return the active data source, chosen by args (assistant context) or env.

    Cached per config tuple so multiple assistants in one process each get a stable
    backend without rebuilding the model every tool call.
    """
    name = (dataset_name or dataset()).strip().lower()
    if name.startswith("synthetic"):
        key = (
            "synthetic",
            model,
            data_prompt_name,
            data_prompt,
            ls_workspace,
            data_gap,
            customer,
            industry,
            pii_focus,
        )
        if key not in _CACHE:
            _CACHE[key] = SyntheticDataSource(
                model=model,
                data_prompt_name=data_prompt_name,
                data_prompt=data_prompt,
                ls_workspace=ls_workspace,
                data_gap=data_gap,
                customer=customer,
                industry=industry,
                pii_focus=pii_focus,
            )
        return _CACHE[key]
    if "static" not in _CACHE:
        _CACHE["static"] = StaticDataSource()
    return _CACHE["static"]
