"""What the SPA's pickers need to know about the customer's LangSmith account.

Workspaces, tracing projects, Context Hub agent repos, the tool catalogue, and the
two URL resolvers behind the "open in LangSmith" links. All read-only lookups whose
only reason to be server-side is that the LangSmith API key must stay here.
"""

from __future__ import annotations

import os

import httpx
from langsmith.utils import LangSmithNotFoundError
from starlette.responses import JSONResponse

from dashboard_agent.config import routing_key, scoped_client
from dashboard_agent.runtime.tools import registry_json
from dashboard_agent.web.errors import route_error


@route_error
async def projects(request):
    """List or create LangSmith projects in a workspace.

    GET [?workspace=<id>]: list project names in a workspace.
    POST {name, workspace?}: create one there (idempotent).
    """
    if request.method != "POST":
        client = scoped_client(request.query_params.get("workspace"))
        return JSONResponse(
            {"projects": sorted({n for p in client.list_projects(limit=200) if (n := p.name)})}
        )

    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)

    scoped_client(body.get("workspace")).create_project(project_name=name, upsert=True)
    return JSONResponse({"ok": True, "name": name})


async def _organization(endpoint: str, key: str) -> str:
    """The org these workspaces belong to. A label, so a failure costs nothing."""
    try:
        async with httpx.AsyncClient(timeout=10) as hc:
            org = await hc.get(f"{endpoint}/api/v1/orgs/current", headers={"x-api-key": key})

        return org.json().get("display_name") or "" if org.status_code == 200 else ""

    except Exception:  # noqa: BLE001 - label only
        return ""


async def workspaces(request):
    """List LangSmith workspaces the routing key can access.

    Uses the org-scoped cross-workspace key when set. If the key is
    workspace-scoped we return an empty list + a note so the SPA degrades to the
    default workspace rather than erroring.
    """
    try:
        # The one route that needs the trace-routing key as a raw header value.
        key = routing_key()
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
        # Which org these workspaces belong to. Only a label, so a failure there must not
        # cost the caller its workspace list: the picker is usable without it.
        return JSONResponse({"workspaces": out, "organization": await _organization(endpoint, key)})
    except Exception as exc:  # noqa: BLE001 - label only
        return JSONResponse({"workspaces": [], "note": f"{type(exc).__name__}: {exc}"})


@route_error
async def agents(request):
    """List a workspace's Context Hub agent repos (for the AGENTS.md prompt picker)."""
    client = scoped_client(request.query_params.get("workspace"))
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


@route_error
async def trace_url(request):
    """Resolve the LangSmith trace URL for a run (for the debug link on answers).

    GET ?run_id=<uuid>[&workspace=<id>]: returns {url}. The run traced to the
    assistant's workspace, so scope the lookup there. Keeps the LangSmith key
    server-side (the SPA only ever gets the resolved URL).
    """
    run_id = request.query_params.get("run_id")
    if not run_id:
        return JSONResponse({"error": "run_id is required"}, status_code=400)

    url = scoped_client(request.query_params.get("workspace")).read_run(run_id).url
    if not url:
        return JSONResponse({"error": "no url for run"}, status_code=404)

    return JSONResponse({"url": url})


@route_error
async def project_url(request):
    """Resolve the LangSmith URL for a tracing project (the header's LangSmith link).

    GET ?project=<name>[&workspace=<id>] -> {url}. The same shape as /trace-url and
    for the same reason: LangSmith URLs need an org and a project id that the SPA
    has no way to know, and resolving them here keeps the API key server-side.

    A project that does not exist yet is a 404 with a plain reason, not an error:
    an assistant that has never been asked a question has no traces, and the
    button should say so rather than open a broken page.
    """
    project = (request.query_params.get("project") or "").strip()
    if not project:
        return JSONResponse({"error": "project is required"}, status_code=400)

    try:
        session = scoped_client(request.query_params.get("workspace")).read_project(
            project_name=project
        )
        url = session.url
    except LangSmithNotFoundError:
        return JSONResponse(
            {"error": f"No traces yet for {project!r}. Ask a question first."}, status_code=404
        )

    if not url:
        return JSONResponse({"error": f"no url for project {project!r}"}, status_code=404)

    return JSONResponse({"url": url})


async def tools(request):
    """List the selectable tool catalogue (labels, groups, defaults).

    Served from the backend registry so adding a capability needs no frontend
    change. Static data — no LangSmith call, no auth.
    """
    return JSONResponse({"tools": registry_json()})
