"""The two core tools: `datasearch` (retrieval) and `push_widget` (dashboard).

Moved out of `agent.py` so the tool catalogue (`registry.py`) can reference every
tool without importing the agent. Behaviour — including the tool docstrings,
which the model sees as the tool descriptions — is unchanged.
"""

from __future__ import annotations

import contextvars
import json

from langchain.tools import ToolRuntime, tool

from ..ctx import ctx_get
from ..datasource import get_datasource
from ..widgets import validate_widget

# Per-invocation collector for widgets emitted by push_widget. Set by
# `agent.run()`; the streaming path re-parses widgets from the token stream
# instead, so it leaves this unset.
widget_sink: contextvars.ContextVar[list[dict] | None] = contextvars.ContextVar(
    "widget_sink", default=None
)


def _datasource_for(runtime: ToolRuntime):
    return get_datasource(
        ctx_get(runtime, "dataset"),
        ctx_get(runtime, "data_model"),
        ctx_get(runtime, "data_prompt_name"),
        ctx_get(runtime, "data_prompt"),
        ctx_get(runtime, "ls_workspace"),
        ctx_get(runtime, "data_gap"),
        ctx_get(runtime, "customer"),
        ctx_get(runtime, "industry"),
        ctx_get(runtime, "pii_focus"),
    )


@tool
def datasearch(query: str, runtime: ToolRuntime) -> str:
    """Look up ANY internal information in natural language.

    This is your system of record. Use it FIRST, before answering, for every
    question about the customer's data: orders, returns, accounts, receipts,
    inventory/stock, tickets, policies, products, metrics, or reports. Describe
    what you need in plain language and include the specifics from the question
    (an order/SKU number, a store or city, a product/model, a timeframe).
    Returns a JSON list of matching records. Each includes:
      - title, source, region, period: for citation
      - text: prose you can quote / summarize
      - data: structured figures you can chart (when relevant)
    Do NOT assume a topic is out of scope: this tool retrieves customer-specific
    and transactional records too, not just aggregate reports.
    """
    results = _datasource_for(runtime).search(query, k=3)
    if not results:
        return json.dumps({"results": [], "note": "No matching reports found."})
    return json.dumps({"results": results}, ensure_ascii=False)


@tool
def push_widget(widget: dict) -> str:
    """Add ONE visualization widget to the live dashboard canvas.

    Call this multiple times to compose a dashboard (e.g. a row of KPIs, then a
    chart, then a table). Only use numbers that came from `datasearch`.

    `widget` must match ONE of these shapes:

    KPI card:
      {"type":"kpi","title":"People reached","value":"2.4M","unit":"people",
       "delta":"+26% vs Q1","trend":"up","description":"coordinated assistance"}

    Bar / Line chart (bar for categories, line for time series):
      {"type":"bar","title":"Funding by sector (US$)","x_label":"Sector",
       "y_label":"USD","series":[{"name":"Q2 2026","points":[
          {"label":"Food & Cash","value":54000000},{"label":"Health","value":28000000}]}]}
      {"type":"line","title":"People reached by month","series":[{"name":"2026",
        "points":[{"label":"Apr","value":720000},{"label":"May","value":810000}]}]}

    Pie chart (exactly one series):
      {"type":"pie","title":"Funding share by sector","series":[{"name":"share",
        "points":[{"label":"Food & Cash","value":54},{"label":"Health","value":28}]}]}

    Table:
      {"type":"table","title":"Available resources","columns":["Resource","Count"],
       "rows":[["Shelter sites","62"],["Mobile health clinics","38"]]}

    Text / key findings:
      {"type":"text","title":"Key findings","content":"- 2.4M people reached ..."}

    Returns a confirmation string.
    """
    normalized = validate_widget(widget)  # raises on malformed input
    sink = widget_sink.get()
    if sink is not None:
        sink.append(normalized)
    title = normalized.get("title", "")
    return f"Added {normalized['type']} widget '{title}' to the dashboard."
