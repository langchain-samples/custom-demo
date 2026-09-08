"""The dashboard tool: `push_widget`.

Moved out of `agent.py` so the tool catalogue (`registry.py`) can reference every
tool without importing the agent. Behaviour — including the tool docstrings,
which the model sees as the tool descriptions — is unchanged.
"""

from __future__ import annotations

import contextvars

from langchain.tools import tool

from dashboard_agent.runtime.widgets import validate_widget

# Per-invocation collector for widgets emitted by push_widget. Set by
# `agent.run()`; the streaming path re-parses widgets from the token stream
# instead, so it leaves this unset.
widget_sink: contextvars.ContextVar[list[dict] | None] = contextvars.ContextVar(
    "widget_sink", default=None
)


@tool
def push_widget(widget: dict) -> str:
    """Add ONE visualization widget to the live dashboard canvas.

    DO NOT CALL THIS if the user's message names an HTML asset, a document, a page, a
    one-pager or a report FILE. That request makes the artifact the deliverable, and the
    dashboard is not a bonus on top of it - it is the thing they did not ask for. This
    holds even when the rest of the message is analytical ("analyze X and recommend Y,
    build an html asset"): the analysis belongs IN the artifact.

    The rule is mechanical on purpose. Saying it in the system prompt was not enough,
    twice, because "should I also build a dashboard?" reads like a judgement call at the
    moment of calling - so the answer lives here, where that decision is actually made.

    Call this multiple times to compose a dashboard (e.g. a row of KPIs, then a
    chart, then a table). Only use numbers you have actually read out of the agent's files.

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
