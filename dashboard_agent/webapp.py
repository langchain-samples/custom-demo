"""Custom routes mounted on Agent Server via langgraph.json `http.app`.

Agent Server serves the graph, threads, and runs. We add just one extra route —
`POST /feedback` — so the SPA can record thumbs up/down against a run's trace
without exposing the LangSmith API key in the browser (the key stays here,
server-side). Served at the same host as the deployment (`:2024` under
`langgraph dev`).
"""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

# Absolute import: Agent Server loads http.app as a top-level module (no package
# parent), so a relative `from .config` import would fail here.
from dashboard_agent.config import make_client


async def feedback(request):
    """Record user feedback on a run (create, or update an existing feedback)."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    run_id = body.get("run_id")
    if not run_id:
        return JSONResponse({"error": "run_id is required"}, status_code=400)

    score = body.get("score")
    comment = body.get("comment") or None
    feedback_id = body.get("feedback_id")
    try:
        client = make_client()
        if feedback_id:
            client.update_feedback(feedback_id, score=score, comment=comment)
            return JSONResponse({"ok": True, "feedback_id": feedback_id})
        fb = client.create_feedback(run_id=run_id, key="user_score", score=score, comment=comment)
        return JSONResponse({"ok": True, "feedback_id": str(getattr(fb, "id", "") or "")})
    except Exception as exc:
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)


app = Starlette(routes=[Route("/feedback", feedback, methods=["POST"])])
