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
from langsmith import Client
from langsmith.utils import LangSmithNotFoundError
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
    # The run's trace is routed to the assistant's workspace (see graph.py); scope
    # the feedback client to that SAME tenant with the cross-workspace key, or the
    # feedback lands in the wrong workspace and a later update 404s.
    workspace = body.get("workspace")

    def _create(client):
        fb = client.create_feedback(run_id=run_id, key="user_score", score=score, comment=comment)
        return str(getattr(fb, "id", "") or "")

    try:
        client = _scoped_client(workspace) if workspace else make_client()
        if feedback_id:
            try:
                client.update_feedback(feedback_id, score=score, comment=comment)
                return JSONResponse({"ok": True, "feedback_id": feedback_id})
            except LangSmithNotFoundError:
                # The original feedback isn't there (e.g. created against another
                # tenant before this fix). Create a fresh one so the comment lands.
                return JSONResponse({"ok": True, "feedback_id": _create(client)})
        return JSONResponse({"ok": True, "feedback_id": _create(client)})
    except Exception as exc:
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)


def _ls_key() -> str:
    """LangSmith key for the trace-routing feature.

    The org-scoped cross-workspace key if set, else the deployment's default key.
    """
    load_env()
    return os.getenv("LS_CROSS_WORKSPACE_KEY") or os.getenv("LANGSMITH_API_KEY") or ""


def _scoped_client(workspace_id: str | None = None):
    """LangSmith client for listing/creating projects in a specific workspace."""
    api_url = os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
    return Client(api_key=_ls_key(), api_url=api_url, workspace_id=workspace_id or None)


async def projects(request):
    """List or create LangSmith projects in a workspace.

    GET [?workspace=<id>]: list project names in a workspace.
    POST {name, workspace?}: create one there (idempotent).
    """
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
            {n for p in client.list_projects(limit=200) if (n := getattr(p, "name", None))}
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


async def agents(request):
    """List a workspace's Context Hub agent repos (for the AGENTS.md prompt picker)."""
    try:
        client = _scoped_client(request.query_params.get("workspace"))
        resp = client.list_agents(limit=100, is_public=False)
        repos = getattr(resp, "repos", None) or []
        names = sorted(
            {
                h
                for r in repos
                if (h := getattr(r, "repo_handle", None) or getattr(r, "full_name", None))
            }
        )
        return JSONResponse({"agents": names})
    except Exception as exc:
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)


async def cleanup(request):
    """Best-effort cascade delete of the LangSmith artifacts an assistant created.

    Body: {workspace, project?, prompt_name?, agent_repo?, skills?[]}. Deletes the
    trace project, the Prompt Hub prompt or Context Hub agent repo, and any linked
    skill repos. Each deletion is independent; failures (e.g. missing perms or an
    already-deleted artifact) are collected rather than aborting the rest.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    client = _scoped_client(body.get("workspace"))
    deleted: list[str] = []
    failed: list[dict] = []

    def _try(kind: str, name: str, fn):
        if not name:
            return
        try:
            fn()
            deleted.append(f"{kind}:{name}")
        except Exception as exc:
            failed.append({"artifact": f"{kind}:{name}", "error": f"{type(exc).__name__}: {exc}"})

    _try(
        "project", body.get("project"), lambda: client.delete_project(project_name=body["project"])
    )
    _try("prompt", body.get("prompt_name"), lambda: client.delete_prompt(body["prompt_name"]))
    _try("agent", body.get("agent_repo"), lambda: client.delete_agent(body["agent_repo"]))
    for skill in body.get("skills") or []:
        _try("skill", skill, lambda s=skill: client.delete_skill(s))

    return JSONResponse({"deleted": deleted, "failed": failed})


async def trace_url(request):
    """Resolve the LangSmith trace URL for a run (for the debug link on answers).

    GET ?run_id=<uuid>[&workspace=<id>]: returns {url}. The run traced to the
    assistant's workspace, so scope the lookup there. Keeps the LangSmith key
    server-side (the SPA only ever gets the resolved URL).
    """
    run_id = request.query_params.get("run_id")
    if not run_id:
        return JSONResponse({"error": "run_id is required"}, status_code=400)
    try:
        client = _scoped_client(request.query_params.get("workspace"))
        url = getattr(client.read_run(run_id), "url", None)
        if not url:
            return JSONResponse({"error": "no url for run"}, status_code=404)
        return JSONResponse({"url": url})
    except Exception as exc:
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)


async def tools(request):
    """List the selectable tool catalogue (labels, groups, defaults).

    Served from the backend registry so adding a capability needs no frontend
    change. Static data — no LangSmith call, no auth.
    """
    from dashboard_agent.tools import registry_json

    return JSONResponse({"tools": registry_json()})


app = Starlette(
    routes=[
        Route("/feedback", feedback, methods=["POST"]),
        Route("/tools", tools, methods=["GET"]),
        Route("/projects", projects, methods=["GET", "POST"]),
        Route("/workspaces", workspaces, methods=["GET"]),
        Route("/hub-prompts", hub_prompts, methods=["GET"]),
        Route("/agents", agents, methods=["GET"]),
        Route("/cleanup", cleanup, methods=["POST"]),
        Route("/trace-url", trace_url, methods=["GET"]),
    ]
)
