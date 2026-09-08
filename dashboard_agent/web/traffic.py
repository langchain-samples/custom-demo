"""Demo traffic (`POST /demo-traffic`, `GET /demo-traffic/status`).

The "one backfill per project" guard and the receipt live in provisioning/traffic.py, not
here, because the automatic backfill at assistant creation does not come through this
route (see the registry comment there). Reading them from that module is what makes the
two paths interlock: this route refuses to start a second backfill over setup's, and the
status route reports setup's while it runs.
"""

from __future__ import annotations

import asyncio

from starlette.responses import JSONResponse

from dashboard_agent.config import scoped_client
from dashboard_agent.provisioning.traffic import (
    SYNTHETIC_TAG,
    demo_traffic_state,
    start_demo_traffic,
)

# Tabs hanging off a tracing project's URL. They are `?tab=<n>` query params on the
# project page — Insights is 3, Engine is 4 — NOT path segments: `<project>/insights`
# is not a route, so both deep links used to land on a broken page. Undocumented UI
# internals, so they stay isolated here: if the indices move this is the only line to
# change, and the fallback is the project page itself, which always works.
_PROJECT_TABS = {"insights": 3, "engine": 4}


async def demo_traffic(request):
    """Backfill a day of synthetic traffic into an assistant's trace project.

    POST {project, workspace?, context?, actions?, data_gap?, customer?} → a receipt.
    Slow (real seed runs + a few thousand run ingests), so `start_demo_traffic` puts it
    on a thread. Exists mainly so assistants created BEFORE the automatic backfill can
    be given traffic without recreating them.
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - an unreadable body is a client error; answer 400, not 500
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    project = (body.get("project") or body.get("ls_project") or "").strip()
    if not project:
        return JSONResponse({"error": "project is required"}, status_code=400)

    ack = start_demo_traffic(
        body.get("workspace") or "",
        project,
        context=body.get("context") or {},
        actions=body.get("actions") or [],
        data_gap=body.get("data_gap") or "",
        customer=body.get("customer") or "",
        seed_traces=body.get("seed_traces") or None,
        with_insights=bool(body.get("with_insights", True)),
        with_engine=bool(body.get("with_engine", True)),
    )
    return JSONResponse(ack, status_code=200 if ack.get("ok") else 500)


def _project_links(workspace: str | None, project: str) -> dict:
    """LangSmith URLs for a trace project and its Insights/Engine tabs.

    Blocking (one read_project round trip) — call it off the event loop.
    """
    session = scoped_client(workspace).read_project(project_name=project)
    base = str(session.url or "").rstrip("/")
    if not base:
        return {}

    # The SDK hands back a bare project URL today, but it is a URL and may grow a
    # query string; appending a second `?` would break every tab link.
    sep = "&" if "?" in base else "?"
    links = {"project": base}
    links.update({name: f"{base}{sep}tab={tab}" for name, tab in _PROJECT_TABS.items()})
    return links


def _synthetic_traffic(workspace: str | None, project: str) -> dict:
    """What LangSmith knows about a project's backfill: `{traces, newest}`.

    Every backfilled run carries the `synthetic-demo` tag (that is the contract in
    demo_traffic's docstring), so one stats call answers "has this project been
    seeded, and when" for real — no matter which process did it or how many times we
    have redeployed since. Root runs only, so this counts TRACES rather than the ~50
    runs each one contains.

    Blocking — call it off the event loop.
    """
    stats = scoped_client(workspace).get_run_stats(
        project_names=[project], is_root=True, filter=f'has(tags, "{SYNTHETIC_TAG}")'
    )
    return {
        "traces": int(stats.get("run_count") or 0),
        "newest": stats.get("last_run_start_time") or "",
    }


async def demo_traffic_status(request):
    """Progress of the last backfill for a project. GET ?project=<name>.

    Two sources, because they answer different questions. `traffic` is derived from
    LangSmith on every call (the `synthetic-demo` tag), so the panel survives a
    reload, a second browser, and a redeploy — it used to read "no backfill recorded
    this session" over a project full of traffic, because the receipt lived in this
    process's memory and every deploy wiped it. `running` and `result` remain the
    in-process receipt: a backfill still on a thread here is the one thing LangSmith
    cannot know.

    Covers the automatic backfill at assistant creation as well as this route's, since
    both register in the same place.
    """
    project = (request.query_params.get("project") or "").strip()
    if not project:
        return JSONResponse({"project": "", "running": False, "links": {}})

    workspace = request.query_params.get("workspace")
    out: dict = {"project": project, **demo_traffic_state(project)}

    async def _read(fn) -> dict:
        """One blocking LangSmith read, off the loop, degrading to an empty answer.

        A project that does not exist yet — no traffic generated — is the common case
        here rather than an error: the count reads as unknown and the links as absent,
        which is exactly the pre-backfill empty state the panel wants to show.
        """
        try:
            return await asyncio.to_thread(fn, workspace, project)
        except Exception:  # noqa: BLE001 - navigation and counts, never state
            return {}

    # Gathered: this route is polled every few seconds while a backfill runs, so the
    # two round trips overlap instead of stacking.
    out["traffic"], out["links"] = await asyncio.gather(
        _read(_synthetic_traffic), _read(_project_links)
    )
    return JSONResponse(out)
