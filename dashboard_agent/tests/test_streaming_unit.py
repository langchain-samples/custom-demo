"""Deterministic unit tests for run_stream's event logic (no LLM).

Mocks agent.stream() to simulate token-level streaming of tool_call_chunks and
message content, verifying:
  - each widget is emitted once, only after ITS args finish streaming
  - tool results (ToolMessage) never leak into answer text
  - answer text streams with a message id (mid) for client-side reset
"""

import json

from langchain_core.messages import AIMessageChunk, ToolMessage

from dashboard_agent.agent import run_stream


def _chunks_for_widget(widget: dict, index: int, call_id: str, pieces: int = 3):
    """Split a push_widget tool call's JSON args into N streaming chunks."""
    args = json.dumps({"widget": widget})
    size = max(1, len(args) // pieces)
    frags = [args[i : i + size] for i in range(0, len(args), size)]
    out = []
    for j, frag in enumerate(frags):
        out.append(
            AIMessageChunk(
                content="",
                tool_call_chunks=[{
                    "name": "push_widget" if j == 0 else None,
                    "args": frag,
                    "id": call_id if j == 0 else None,
                    "index": index,
                    "type": "tool_call_chunk",
                }],
            )
        )
    return out


class FakeAgent:
    def __init__(self, messages):
        self._messages = messages

    def stream(self, _inp, config=None, stream_mode=None):
        for m in self._messages:
            yield (m, {})


def _build_events():
    kpi = {"type": "kpi", "title": "People reached", "value": "2.4M"}
    bar = {"type": "bar", "title": "Funding", "series": [{"name": "Q2", "points": [{"label": "Health", "value": 28}]}]}
    messages = []
    messages += _chunks_for_widget(kpi, index=0, call_id="call_a")
    messages += _chunks_for_widget(bar, index=1, call_id="call_b")
    # A tool result flowing through the messages stream — must be ignored.
    messages.append(ToolMessage(content='{"columns":["x"],"rows":[{"x":1}]}', tool_call_id="call_a"))
    # Final answer streams as AI content with a stable id.
    messages.append(AIMessageChunk(content="Egypt aid reached ", id="answer-1"))
    messages.append(AIMessageChunk(content="2.4M people.", id="answer-1"))
    return messages


def test_stream_emits_widgets_incrementally_and_clean_answer():
    events = list(run_stream("q", agent=FakeAgent(_build_events())))
    types = [e["type"] for e in events]

    # Two widgets, each emitted once; answer deltas; done last.
    assert types.count("widget") == 2
    assert types[-1] == "done"

    widgets = [e["widget"] for e in events if e["type"] == "widget"]
    assert widgets[0]["type"] == "kpi" and widgets[0]["value"] == "2.4M"
    assert widgets[1]["type"] == "bar"

    # Widgets are emitted before the answer finishes (progressive build).
    assert types.index("widget") < types.index("answer_delta")

    # No tool-result JSON leaked into the answer.
    answer = "".join(e["text"] for e in events if e["type"] == "answer_delta")
    assert answer == "Egypt aid reached 2.4M people."
    assert "columns" not in answer and "rows" not in answer

    # answer deltas carry the message id for client-side reset.
    mids = {e.get("mid") for e in events if e["type"] == "answer_delta"}
    assert mids == {"answer-1"}


def test_partial_args_do_not_emit_early():
    # A single widget split into many pieces must still emit exactly once.
    kpi = {"type": "kpi", "title": "Funded", "value": "68%"}
    agent = FakeAgent(_chunks_for_widget(kpi, index=0, call_id="c1", pieces=8))
    events = list(run_stream("q", agent=agent))
    assert [e["type"] for e in events].count("widget") == 1


def test_preamble_is_reset_when_tools_start():
    # Model narrates a preamble in a message that then calls a tool; the final
    # summary is a separate message. Client should see answer_reset and end with
    # only the summary.
    kpi = {"type": "kpi", "title": "Coverage", "value": "94%"}
    args = json.dumps({"widget": kpi})
    msgs = [
        AIMessageChunk(content="I'll gather the data and build a dashboard.", id="p1"),
        AIMessageChunk(
            content="", id="p1",
            tool_call_chunks=[{"name": "push_widget", "args": args, "id": "c1", "index": 0, "type": "tool_call_chunk"}],
        ),
        AIMessageChunk(content="Canada has 94% national water coverage.", id="final"),
    ]
    events = list(run_stream("q", agent=FakeAgent(msgs)))
    types = [e["type"] for e in events]
    assert "answer_reset" in types
    assert types.count("widget") == 1

    # Reconstruct the answer the way the client does (reset clears the buffer).
    answer, mid = "", None
    for e in events:
        if e["type"] == "answer_reset":
            answer, mid = "", None
        elif e["type"] == "answer_delta":
            if e.get("mid") and e["mid"] != mid:
                mid = e["mid"]; answer = ""
            answer += e["text"]
    assert answer == "Canada has 94% national water coverage."


def test_non_widget_tools_emit_tool_events():
    # datasearch and query_sql calls should surface as tool-activity events.
    ds = AIMessageChunk(
        content="",
        tool_call_chunks=[{
            "name": "datasearch",
            "args": json.dumps({"query": "Iran displaced families resources"}),
            "id": "d1", "index": 0, "type": "tool_call_chunk",
        }],
    )
    sql = AIMessageChunk(
        content="",
        tool_call_chunks=[{
            "name": "query_sql",
            "args": json.dumps({"query": "SELECT * FROM iran_resources"}),
            "id": "s1", "index": 1, "type": "tool_call_chunk",
        }],
    )
    events = list(run_stream("q", agent=FakeAgent([ds, sql])))
    tools = [e for e in events if e["type"] == "tool"]
    assert [t["name"] for t in tools] == ["datasearch", "query_sql"]
    assert tools[0]["summary"] == "Iran displaced families resources"
    assert "SELECT" in tools[1]["summary"]
    # tool calls carry an id so results can be matched to their chip
    assert tools[0]["id"] == "d1"


def test_tool_results_stream_and_match_by_id():
    ds = AIMessageChunk(
        content="",
        tool_call_chunks=[{
            "name": "datasearch",
            "args": json.dumps({"query": "Iran resources"}),
            "id": "d1", "index": 0, "type": "tool_call_chunk",
        }],
    )
    result = ToolMessage(content='{"results": [{"region": "Iran"}]}', name="datasearch", tool_call_id="d1")
    # push_widget results must NOT surface as tool_result events.
    widget_result = ToolMessage(content="Added kpi widget", name="push_widget", tool_call_id="w1")
    events = list(run_stream("q", agent=FakeAgent([ds, result, widget_result])))
    tool_results = [e for e in events if e["type"] == "tool_result"]
    assert len(tool_results) == 1
    assert tool_results[0]["id"] == "d1"
    assert "Iran" in tool_results[0]["content"]


def test_malformed_widget_is_skipped():
    bad = AIMessageChunk(
        content="",
        tool_call_chunks=[{
            "name": "push_widget",
            "args": json.dumps({"widget": {"type": "kpi", "title": "no value"}}),  # missing value
            "id": "c1", "index": 0, "type": "tool_call_chunk",
        }],
    )
    types = [e["type"] for e in run_stream("q", agent=FakeAgent([bad]))]
    assert "widget" not in types  # malformed spec skipped
    assert types[-1] == "done"
