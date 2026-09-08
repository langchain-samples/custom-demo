"""`POST /feedback`: thumbs up/down on a run's trace, recorded server-side.

Its own module because it is the one route that writes to LangSmith on behalf of
the end user, and the only one that has to reason about which tenant a run was
traced to.
"""

from __future__ import annotations

from langsmith.utils import LangSmithNotFoundError
from starlette.responses import JSONResponse

from dashboard_agent.config import make_client, scoped_client
from dashboard_agent.web.errors import route_error


@route_error
async def feedback(request):
    """Record user feedback on a run (create, or update an existing feedback)."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - an unreadable body is a client error; answer 400, not 500
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    run_id = body.get("run_id")
    if not run_id:
        return JSONResponse({"error": "run_id is required"}, status_code=400)

    score = body.get("score")
    comment = body.get("comment") or None
    feedback_id = body.get("feedback_id")
    # The run's trace is routed to the assistant's workspace (see graph.py); scope
    # the feedback client to that SAME tenant with the cross-workspace key, or the
    # feedback lands in the wrong workspace and a later update 404s.
    workspace = body.get("workspace")

    def _create(client):
        fb = client.create_feedback(run_id=run_id, key="user_score", score=score, comment=comment)
        return str(fb.id or "")

    client = scoped_client(workspace) if workspace else make_client()
    if not feedback_id:
        return JSONResponse({"ok": True, "feedback_id": _create(client)})

    try:
        client.update_feedback(feedback_id, score=score, comment=comment)
    except LangSmithNotFoundError:
        # The original feedback isn't there (e.g. created against another
        # tenant before this fix). Create a fresh one so the comment lands.
        return JSONResponse({"ok": True, "feedback_id": _create(client)})

    return JSONResponse({"ok": True, "feedback_id": feedback_id})
