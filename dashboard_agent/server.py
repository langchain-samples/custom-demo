"""FastAPI server: chat endpoint + static dashboard SPA.

POST /api/chat  {"question": "..."} -> {"answer": str, "widgets": [...]}
GET  /api/health                    -> {"status": "ok"}
GET  /                              -> the dashboard single-page app
"""

from __future__ import annotations

import json
import traceback
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent import run, run_stream
from .config import make_client

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Dashboard Agent", version="0.1.0")


class ChatRequest(BaseModel):
    question: str
    thread_id: str = "demo"


class FeedbackRequest(BaseModel):
    run_id: str
    score: float  # 1 = thumbs up, 0 = thumbs down
    comment: str | None = None
    feedback_id: str | None = None  # when set, update the existing feedback


_LS_CLIENT = None


def _ls_client():
    global _LS_CLIENT
    if _LS_CLIENT is None:
        _LS_CLIENT = make_client()
    return _LS_CLIENT


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/chat")
def chat(req: ChatRequest) -> JSONResponse:
    question = (req.question or "").strip()
    if not question:
        return JSONResponse(
            {"error": "question is required"}, status_code=400
        )
    try:
        result = run(question, thread_id=req.thread_id)
        return JSONResponse(result)
    except Exception as exc:  # surface a clean error to the UI
        traceback.print_exc()
        return JSONResponse(
            {"error": f"{type(exc).__name__}: {exc}"}, status_code=500
        )


@app.post("/api/chat/stream")
def chat_stream(req: ChatRequest):
    """Stream the run as newline-delimited JSON (NDJSON) events.

    The dashboard builds progressively: widget events arrive as they are made,
    then the answer streams in, then a final {"type":"done"}.
    """
    question = (req.question or "").strip()
    if not question:
        return JSONResponse({"error": "question is required"}, status_code=400)

    def gen():
        try:
            for event in run_stream(question, thread_id=req.thread_id):
                yield json.dumps(event, ensure_ascii=False) + "\n"
        except Exception as exc:  # pragma: no cover - defensive
            traceback.print_exc()
            yield json.dumps({"type": "error", "error": f"{type(exc).__name__}: {exc}"}) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.post("/api/feedback")
def feedback(req: FeedbackRequest) -> JSONResponse:
    """Record thumbs up/down + comment against the run's LangSmith trace."""
    if not req.run_id:
        return JSONResponse({"error": "run_id is required"}, status_code=400)
    try:
        client = _ls_client()
        if req.feedback_id:
            # Update the feedback created on the initial thumb click.
            client.update_feedback(req.feedback_id, score=req.score, comment=(req.comment or None))
            return JSONResponse({"ok": True, "feedback_id": req.feedback_id})
        fb = client.create_feedback(
            run_id=req.run_id,
            key="user_score",
            score=req.score,
            comment=(req.comment or None),
        )
        return JSONResponse({"ok": True, "feedback_id": str(getattr(fb, "id", "") or "")})
    except Exception as exc:
        traceback.print_exc()
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)


# Mount the SPA last so /api/* routes take precedence.
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
