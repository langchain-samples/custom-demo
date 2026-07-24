"""Server contract tests with the agent mocked (fast, no LLM calls)."""

from fastapi.testclient import TestClient

import dashboard_agent.server as server


def _fake_run(question, thread_id="demo"):
    return {
        "question": question,
        "answer": "Mocked answer citing OCHA.",
        "run_id": "run-1",
        "widgets": [
            {"type": "kpi", "title": "People reached", "value": "2.4M"},
            {
                "type": "bar",
                "title": "Funding",
                "series": [{"name": "Q2", "points": [{"label": "Health", "value": 28}]}],
            },
        ],
    }


client = TestClient(server.app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_chat_contract(monkeypatch):
    monkeypatch.setattr(server, "run", _fake_run)
    r = client.post("/api/chat", json={"question": "impact in Egypt?"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"]
    assert len(body["widgets"]) == 2
    assert {w["type"] for w in body["widgets"]} == {"kpi", "bar"}


def test_empty_question_rejected():
    r = client.post("/api/chat", json={"question": "   "})
    assert r.status_code == 400


def test_agent_error_surfaced(monkeypatch):
    def boom(question, thread_id="demo"):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(server, "run", boom)
    r = client.post("/api/chat", json={"question": "x"})
    assert r.status_code == 500
    assert "model exploded" in r.json()["error"]


def test_spa_served():
    r = client.get("/")
    assert r.status_code == 200
    assert "Dashboard Agent" in r.text
    r2 = client.get("/app.js")
    assert r2.status_code == 200
    assert "chartConfig" in r2.text


def _fake_stream(question, thread_id="demo"):
    yield {"type": "widget", "widget": {"type": "kpi", "title": "People reached", "value": "2.4M"}}
    yield {"type": "widget", "widget": {"type": "bar", "title": "F", "series": [{"name": "Q2", "points": [{"label": "H", "value": 28}]}]}}
    yield {"type": "answer_delta", "text": "Egypt aid ", "mid": "m1"}
    yield {"type": "answer_delta", "text": "reached 2.4M.", "mid": "m1"}
    yield {"type": "done"}


def test_stream_contract(monkeypatch):
    import json

    monkeypatch.setattr(server, "run_stream", _fake_stream)
    r = client.post("/api/chat/stream", json={"question": "impact in Egypt?"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-ndjson")
    events = [json.loads(line) for line in r.text.splitlines() if line.strip()]
    types = [e["type"] for e in events]
    assert types == ["widget", "widget", "answer_delta", "answer_delta", "done"]
    # widgets stream before the answer completes; final event is done
    assert types.index("widget") < types.index("answer_delta")
    assert types[-1] == "done"


def test_stream_empty_question_rejected():
    r = client.post("/api/chat/stream", json={"question": ""})
    assert r.status_code == 400


class _FakeFeedback:
    id = "fb-1"


def test_feedback_posts_to_langsmith(monkeypatch):
    calls = []

    class FakeClient:
        def create_feedback(self, **kwargs):
            calls.append(kwargs)
            return _FakeFeedback()

    monkeypatch.setattr(server, "_ls_client", lambda: FakeClient())
    r = client.post("/api/feedback", json={"run_id": "abc-123", "score": 1, "comment": "great"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.json()["feedback_id"] == "fb-1"
    assert calls[0]["run_id"] == "abc-123"
    assert calls[0]["score"] == 1
    assert calls[0]["comment"] == "great"
    assert calls[0]["key"] == "user_score"


def test_feedback_update_path(monkeypatch):
    updates = []

    class FakeClient:
        def create_feedback(self, **kwargs):
            raise AssertionError("should not create when feedback_id is provided")

        def update_feedback(self, feedback_id, **kwargs):
            updates.append((feedback_id, kwargs))

    monkeypatch.setattr(server, "_ls_client", lambda: FakeClient())
    r = client.post(
        "/api/feedback",
        json={"run_id": "abc-123", "score": 0, "comment": "wrong number", "feedback_id": "fb-1"},
    )
    assert r.status_code == 200 and r.json()["ok"] is True
    assert updates[0][0] == "fb-1"
    assert updates[0][1]["score"] == 0
    assert updates[0][1]["comment"] == "wrong number"


def test_feedback_requires_run_id():
    r = client.post("/api/feedback", json={"run_id": "", "score": 0})
    assert r.status_code == 400


def test_feedback_error_surfaced(monkeypatch):
    class BadClient:
        def create_feedback(self, **kwargs):
            raise RuntimeError("ls down")

    monkeypatch.setattr(server, "_ls_client", lambda: BadClient())
    r = client.post("/api/feedback", json={"run_id": "x", "score": 1})
    assert r.status_code == 500 and "ls down" in r.json()["error"]
