"""The Dashboard Agent deep agent.

The agent has two custom tools:

  * ``datasearch``   — dummy in-memory RAG over situation reports/assessments.
  * ``push_widget``  — appends ONE validated visualization to the live dashboard.

A run works like the CopilotKit "shared-state canvas" pattern: the agent
searches for grounded data, then emits a series of widgets that compose a
persistent dashboard, and finally writes a short narrative answer. Emitted
widgets are collected per-invocation via a ContextVar sink so the server can
return them alongside the chat text.
"""

from __future__ import annotations

import contextvars
import json
import uuid
from typing import Any

from langchain.agents.middleware import ModelRequest, dynamic_prompt
from langchain.tools import tool

from .config import MODEL, require_anthropic_key
from .database import SCHEMA_DESCRIPTION, run_query
from .prompt import pull_system_prompt
from .rag import search
from .widgets import validate_widget

# Per-invocation collector for widgets emitted by push_widget.
_widget_sink: contextvars.ContextVar[list[dict] | None] = contextvars.ContextVar(
    "widget_sink", default=None
)


@tool
def datasearch(query: str) -> str:
    """Search situation reports and assessments for grounded facts and data.

    Use this FIRST, before answering, to retrieve relevant report excerpts.
    Returns a JSON list of matching documents. Each document includes:
      - title, source, region, period: for citation
      - text: prose you can quote / summarize
      - data: a structured block of numbers you can turn into dashboard widgets
    Search with specific terms (region + topic), e.g. "Egypt humanitarian aid impact".
    """
    results = search(query, k=3)
    if not results:
        return json.dumps({"results": [], "note": "No matching reports found."})
    return json.dumps({"results": results}, ensure_ascii=False)


@tool
def query_sql(query: str) -> str:
    """Run a read-only SQL SELECT against the structured database.

    Use this when you need precise, aggregated, or filtered numbers to chart
    (totals, rankings, deltas, time series). Only SELECT is allowed. Returns a
    JSON object with `columns`, `rows`, and `row_count`, or an `error`.
    """
    return json.dumps(run_query(query), ensure_ascii=False)


# Append the live DB schema to what the model sees (docstrings must be literals).
query_sql.description = (
    query_sql.description
    + "\n\n"
    + SCHEMA_DESCRIPTION
    + "\n\nExample: SELECT sector, funding_usd FROM egypt_sector_funding ORDER BY funding_usd DESC"
)


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
    sink = _widget_sink.get()
    if sink is not None:
        sink.append(normalized)
    title = normalized.get("title", "")
    return f"Added {normalized['type']} widget '{title}' to the dashboard."


# The system prompt is sourced from LangSmith Prompt Hub (see prompt.py). We pull
# it once at the start of each question and stash it in this ContextVar, so every
# model call within one run sees a consistent prompt while a fresh question always
# re-pulls — that is what lets you fix the planted bug live in the Hub, no restart.
_prompt_override: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "prompt_override", default=None
)


@dynamic_prompt
def _hub_system_prompt(request: ModelRequest) -> str:
    """Inject the system prompt for each model call from the Hub-sourced text.

    Uses the per-run pulled value when present (set by run/run_stream); otherwise
    pulls directly (e.g. when the agent is invoked outside those helpers).
    """
    override = _prompt_override.get()
    return override if override is not None else pull_system_prompt()


def build_agent(model: str | None = None):
    """Construct the deep agent. Requires ANTHROPIC_API_KEY.

    The system prompt is injected dynamically from Prompt Hub via the
    `_hub_system_prompt` middleware, so it is never baked in at build time.
    """
    require_anthropic_key()
    from deepagents import create_deep_agent
    from langchain_anthropic import ChatAnthropic
    from langgraph.checkpoint.memory import MemorySaver

    # Build an explicit model so we can harden it against transient API
    # overload (HTTP 529): more retries with exponential backoff, longer timeout.
    llm = ChatAnthropic(
        model=model or MODEL,
        max_retries=8,
        timeout=120,
        max_tokens=8000,
    )

    # In-memory checkpointer so a thread_id carries conversation memory
    # (follow-up questions in the same browser session). Resets on restart.
    return create_deep_agent(
        model=llm,
        tools=[datasearch, query_sql, push_widget],
        middleware=[_hub_system_prompt],
        checkpointer=MemorySaver(),
    )


# One lazily-built agent — the prompt is dynamic, so there is no longer a
# per-variant cache (the old buggy / fixed split is gone).
_AGENT: Any = None


def get_agent():
    global _AGENT
    if _AGENT is None:
        _AGENT = build_agent()
    return _AGENT


def _final_text(result: dict[str, Any]) -> str:
    """Extract the assistant's final textual answer from an agent result."""
    messages = result.get("messages", [])
    for msg in reversed(messages):
        # AIMessage with no tool calls == the final answer.
        role = getattr(msg, "type", None) or (msg.get("role") if isinstance(msg, dict) else None)
        if role in ("ai", "assistant"):
            content = getattr(msg, "content", None)
            if isinstance(msg, dict):
                content = msg.get("content")
            tool_calls = getattr(msg, "tool_calls", None) or (
                msg.get("tool_calls") if isinstance(msg, dict) else None
            )
            text = _content_to_text(content)
            if text and not tool_calls:
                return text
    # Fallback: last AI text of any kind.
    for msg in reversed(messages):
        content = getattr(msg, "content", None)
        if isinstance(msg, dict):
            content = msg.get("content")
        text = _content_to_text(content)
        if text:
            return text
    return ""


def _content_to_text(content: Any, strip: bool = True) -> str:
    # strip=True for whole messages; strip=False for streaming deltas, where
    # trimming each token would delete the spaces between words.
    def _fin(s: str) -> str:
        return s.strip() if strip else s

    if content is None:
        return ""
    if isinstance(content, str):
        return _fin(content)
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return _fin("".join(parts))
    return _fin(str(content))


def run(question: str, thread_id: str = "demo", agent=None) -> dict[str, Any]:
    """Run one question through the agent.

    Returns {"answer": str, "widgets": [ ... ], "question": str}.
    Widgets are collected via a per-invocation ContextVar sink. The system prompt
    is pulled fresh from Prompt Hub for this run and pinned via a ContextVar.
    """
    agent = agent or get_agent()
    sink: list[dict] = []
    token = _widget_sink.set(sink)
    prompt_token = _prompt_override.set(pull_system_prompt())
    # Assign the trace root run id ourselves so feedback attaches to the TRACE,
    # not a child LLM/tool span.
    run_id = str(uuid.uuid4())
    try:
        result = agent.invoke(
            {"messages": [{"role": "user", "content": question}]},
            config={"run_id": run_id, "configurable": {"thread_id": thread_id}},
        )
    finally:
        _widget_sink.reset(token)
        _prompt_override.reset(prompt_token)
    return {"question": question, "answer": _final_text(result), "widgets": sink, "run_id": run_id}


def run_stream(question: str, thread_id: str = "demo", agent=None):
    """Stream a run as a sequence of event dicts, in real time.

    Yields, in the order they occur during the agent run:
      {"type": "tool", "name": ..., "summary": ...}  as each non-widget tool is called
      {"type": "widget", "widget": {...}}            as each push_widget call is made
      {"type": "answer_delta", "text": ...}          as the final answer is generated
      {"type": "answer_reset"}                        to drop a pre-tool preamble
      {"type": "run_id", "run_id": ...}              the LangSmith trace id (for feedback)
      {"type": "done"}                                when the run completes
      {"type": "error", "error": ...}                 on failure

    Widgets and tool calls are parsed from the token-level tool-call stream and
    emitted the instant each call's args finish streaming (Anthropic streams
    tool_use blocks one after another) — giving a progressive dashboard build and
    a live tool-activity feed.
    """
    from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

    agent = agent or get_agent()

    tool_bufs: dict[Any, dict[str, str]] = {}
    emitted: set = set()
    text_mids: set = set()   # message ids that streamed answer text
    reset_mids: set = set()  # message ids we've already reset (preamble)

    def _tool_summary(name: str, parsed: dict) -> str:
        if name in ("datasearch", "query_sql"):
            return str(parsed.get("query", ""))
        # Compact one-liner for any other tool (e.g. write_todos, task).
        return json.dumps(parsed, ensure_ascii=False)[:120]

    # Assign the trace root run id ourselves (via config["run_id"]) so feedback
    # attaches to the TRACE root, not a child LLM/tool span. This also avoids the
    # ContextVar issues of collect_runs() inside a streaming generator.
    root_id = str(uuid.uuid4())
    config = {"run_id": root_id, "configurable": {"thread_id": thread_id}}

    # Pull the prompt once for this question and pin it for every model call.
    prompt_token = _prompt_override.set(pull_system_prompt())
    try:
        for msg, _meta in agent.stream(
            {"messages": [{"role": "user", "content": question}]},
            config=config,
            stream_mode="messages",
        ):
            # Tool results -> a tool_result event the client attaches to the
            # matching tool chip (so it can be expanded). Skip push_widget results.
            if isinstance(msg, ToolMessage):
                tname = getattr(msg, "name", "") or ""
                if tname != "push_widget":
                    content = _content_to_text(getattr(msg, "content", None))
                    yield {
                        "type": "tool_result",
                        "id": getattr(msg, "tool_call_id", None),
                        "name": tname,
                        "content": content[:8000],
                    }
                continue

            # Otherwise only AI messages (never leak raw tool JSON into the answer).
            if not isinstance(msg, (AIMessage, AIMessageChunk)):
                continue

            mid = getattr(msg, "id", None)
            text = _content_to_text(getattr(msg, "content", None), strip=False)
            if text:
                text_mids.add(mid)
                # `mid` lets the client reset per message so pre-tool narration
                # is replaced by the final answer.
                yield {"type": "answer_delta", "text": text, "mid": mid}

            tccs = getattr(msg, "tool_call_chunks", None) or []
            # If this message narrated text and is now calling tools, it was a
            # preamble ("I'll build a dashboard…") — tell the client to drop it.
            if tccs and mid in text_mids and mid not in reset_mids:
                reset_mids.add(mid)
                yield {"type": "answer_reset"}

            for tcc in tccs:
                idx = tcc.get("index")
                if idx is None:
                    idx = tcc.get("id")
                buf = tool_bufs.setdefault(idx, {"name": "", "args": "", "id": ""})
                if tcc.get("name"):
                    buf["name"] = tcc["name"]
                if tcc.get("id"):
                    buf["id"] = tcc["id"]
                if tcc.get("args"):
                    buf["args"] += tcc["args"]

                key = buf.get("id") or idx
                if key in emitted:
                    continue
                try:
                    parsed = json.loads(buf["args"])
                except (ValueError, TypeError):
                    continue  # args not fully streamed yet
                if not isinstance(parsed, dict):
                    continue
                name = buf.get("name") or ""
                if name == "push_widget":
                    spec = parsed.get("widget", parsed)
                    if not isinstance(spec, dict):
                        continue
                    try:
                        widget = validate_widget(spec)
                    except Exception:
                        continue  # incomplete/invalid so far — wait for more
                    emitted.add(key)
                    yield {"type": "widget", "widget": widget}
                else:
                    # datasearch / query_sql / built-in tools -> activity feed.
                    emitted.add(key)
                    yield {
                        "type": "tool",
                        "name": name,
                        "summary": _tool_summary(name, parsed),
                        "id": buf.get("id") or str(key),
                    }

        yield {"type": "run_id", "run_id": root_id}
        yield {"type": "done"}
    except Exception as exc:  # surface upstream/model errors to the client
        yield {"type": "error", "error": f"{type(exc).__name__}: {exc}"}
    finally:
        _prompt_override.reset(prompt_token)
