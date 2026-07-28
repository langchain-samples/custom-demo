"""Custom routes mounted on Agent Server via langgraph.json `http.app`.

Agent Server serves the graph, threads, and runs. We add two extra routes —
`POST /feedback` (record thumbs up/down on a run's trace) and `GET /projects`
(list LangSmith tracing projects for the trace-routing picker) — so the SPA never
sees the LangSmith API key (it stays here, server-side). Served at the same host
as the deployment (`:2024` under `langgraph dev`).
"""

from __future__ import annotations

import os

import httpx
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

# Absolute import: Agent Server loads http.app as a top-level module (no package
# parent), so a relative `from .config` import would fail here.
from dashboard_agent.config import load_env, make_client


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


def _ls_key() -> str:
    """LangSmith key for the trace-routing feature: the org-scoped cross-workspace
    key if set, else the deployment's default key."""
    load_env()
    return os.getenv("LS_CROSS_WORKSPACE_KEY") or os.getenv("LANGSMITH_API_KEY") or ""


def _scoped_client(workspace_id: str | None = None):
    """LangSmith client for listing/creating projects in a specific workspace."""
    from langsmith import Client

    api_url = os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
    return Client(api_key=_ls_key(), api_url=api_url, workspace_id=workspace_id or None)


async def projects(request):
    """GET [?workspace=<id>]: list project names in a workspace.
    POST {name, workspace?}: create one there (idempotent)."""
    try:
        if request.method == "POST":
            body = await request.json()
            name = (body.get("name") or "").strip()
            if not name:
                return JSONResponse({"error": "name is required"}, status_code=400)
            client = _scoped_client(body.get("workspace"))
            client.create_project(project_name=name, upsert=True)
            return JSONResponse({"ok": True, "name": name})
        client = _scoped_client(request.query_params.get("workspace"))
        names = sorted(
            {
                n
                for p in client.list_projects(limit=200)
                if (n := getattr(p, "name", None))
            }
        )
        return JSONResponse({"projects": names})
    except Exception as exc:
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)


async def workspaces(request):
    """List LangSmith workspaces the routing key can access.

    Uses the org-scoped cross-workspace key when set. If the key is
    workspace-scoped we return an empty list + a note so the SPA degrades to the
    default workspace rather than erroring.
    """
    try:
        key = _ls_key()
        endpoint = os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com").rstrip("/")
        async with httpx.AsyncClient(timeout=20) as hc:
            resp = await hc.get(f"{endpoint}/api/v1/workspaces", headers={"x-api-key": key})
        if resp.status_code != 200:
            return JSONResponse(
                {"workspaces": [], "note": f"key may be workspace-scoped (HTTP {resp.status_code})"}
            )
        out = [
            {"id": w.get("id"), "name": w.get("display_name") or w.get("name") or w.get("id")}
            for w in resp.json()
            if w.get("id")
        ]
        out.sort(key=lambda w: (w["name"] or "").lower())
        return JSONResponse({"workspaces": out})
    except Exception as exc:
        return JSONResponse({"workspaces": [], "note": f"{type(exc).__name__}: {exc}"})


async def hub_prompts(request):
    """List a workspace's own Prompt Hub prompt handles (for the system-prompt picker)."""
    try:
        client = _scoped_client(request.query_params.get("workspace"))
        # is_public=False → only this workspace's own prompts (public Hub prompts
        # otherwise leak across every tenant).
        resp = client.list_prompts(limit=100, is_public=False)
        repos = getattr(resp, "repos", None) or []
        names = sorted(
            {
                h
                for r in repos
                if (h := getattr(r, "repo_handle", None) or getattr(r, "full_name", None))
            }
        )
        return JSONResponse({"prompts": names})
    except Exception as exc:
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)


app = Starlette(
    routes=[
        Route("/feedback", feedback, methods=["POST"]),
        Route("/projects", projects, methods=["GET", "POST"]),
        Route("/workspaces", workspaces, methods=["GET"]),
        Route("/hub-prompts", hub_prompts, methods=["GET"]),
    ]
)
