"""Custom routes mounted on Agent Server via langgraph.json `http.app`.

Agent Server serves the graph, threads, and runs. We add extra routes —
`POST /feedback` (record thumbs up/down on a run's trace), `GET /projects`
(list LangSmith tracing projects for the trace-routing picker),
`GET /sandbox-files` + `GET /sandbox-file` (browse/read the assistant's sandbox
VM for the SPA's file dialog), and `POST /evals/run` + `GET /evals/status` (kick
off and read back the assistant's per-assistant eval experiment) — so the SPA
never sees the LangSmith API key (it stays here, server-side). Served at the same
host as the deployment (`:2024` under `langgraph dev`).
"""

from __future__ import annotations

import asyncio
import copy
import os
import posixpath
import threading
import time
import traceback

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

    Body: {workspace, project?, prompt_name?, agent_repo?, skills?[], eval_dataset?}.
    Deletes the trace project, the Prompt Hub prompt or Context Hub agent repo, any
    linked skill repos, and the assistant's eval dataset. Each deletion is
    independent; failures (e.g. missing perms or an already-deleted artifact) are
    collected rather than aborting the rest.
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
    # The skills bundle is an agent-type repo (push_agent) → delete_agent, not delete_skill.
    _try(
        "skills bundle",
        body.get("skills_repo"),
        lambda: client.delete_agent(body["skills_repo"]),
    )
    for skill in body.get("skills") or []:  # legacy per-skill repos
        _try("skill", skill, lambda s=skill: client.delete_skill(s))
    # The per-assistant eval dataset (assistant_setup writes it into
    # `ls_artifacts.eval_dataset`). Absent for assistants created before that
    # feature, and `_try` no-ops on a falsy name — so this stays a silent skip.
    _try(
        "eval dataset",
        body.get("eval_dataset"),
        lambda: client.delete_dataset(dataset_name=body["eval_dataset"]),
    )
    # The run rule that attached the judge to that dataset. Deleted explicitly because
    # dropping the dataset is not documented to cascade to it, and a leftover rule shows
    # up as a stray evaluator in the customer's workspace.
    _try(
        "eval evaluator",
        body.get("eval_rule_id"),
        lambda: _delete_eval_rule(body.get("workspace"), body["eval_rule_id"]),
    )
    # The judge prompt the rule pointed at. Deleted after the rule, since a prompt with
    # a live reference may refuse to go.
    _try(
        "eval judge prompt",
        body.get("eval_judge_prompt"),
        lambda: client.delete_prompt(body["eval_judge_prompt"]),
    )

    return JSONResponse({"deleted": deleted, "failed": failed})


def _delete_eval_rule(workspace: str | None, rule_id: str) -> None:
    """DELETE the run rule `rule_id`. Raises on failure, so `_try` records it."""
    from dashboard_agent.assistant_evals import _rules_api

    url, headers = _rules_api(workspace)
    res = httpx.delete(f"{url}/{rule_id}", headers=headers, timeout=30)
    res.raise_for_status()


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


# --- per-assistant evals (POST /evals/run, GET /evals/status) -------------------
#
# Setup provisions a LangSmith dataset per assistant (see assistant_evals); these two
# routes are the SPA's handle on it — start an experiment, read the latest score.
# LangSmith is the SOURCE OF TRUTH: /evals/status re-derives the score from the
# dataset's experiments on every call, so the panel survives a page reload, a second
# browser, and a redeploy mid-demo. The only state kept here is what LangSmith cannot
# know — that a run is on a thread in THIS process, and why the last one died (see
# _INFLIGHT / _LAST_RUN_ERROR); nothing correct depends on either surviving.
#
# An experiment with fewer scored rows than the dataset has examples is either still
# running (feedback lands as rows finish) or died partway. Past this long we call it
# finished and report the partial score, rather than spinning the panel forever on a
# run that is never coming back.
_RUN_STALE_SECS = 900

# The two things LangSmith cannot tell us, kept per dataset name:
#   _INFLIGHT       when a run was spawned here and has not returned. Covers the
#                   seconds between the click and the experiment project appearing,
#                   and lets a second click (or a second browser) be refused instead
#                   of spending another three agent runs.
#   _LAST_RUN_ERROR why the last run died. The likeliest failures — no Anthropic key,
#                   a workspace this key cannot see — happen while BUILDING the
#                   target, before `client.evaluate` has created a project, so there
#                   is no trace to read and LangSmith still shows the previous,
#                   complete experiment. Without this the presenter fixes the prompt,
#                   clicks Run, and watches a number that never changes.
# Hints only: the SCORE is always re-derived from LangSmith, and losing these on a
# redeploy costs nothing.
_RUN_LOCK = threading.Lock()
_INFLIGHT: dict[str, float] = {}
_LAST_RUN_ERROR: dict[str, str] = {}


def _inflight_since(dataset: str) -> float:
    """When the in-process run for `dataset` started, 0.0 if none is live.

    Same staleness cutoff as the LangSmith-derived `running`: a thread killed by a
    process restart mid-run would otherwise block the Run button forever.
    """
    with _RUN_LOCK:
        started = _INFLIGHT.get(dataset, 0.0)
    return started if started and time.time() - started < _RUN_STALE_SECS else 0.0


def _started_ts(session) -> float:
    """Experiment start as a POSIX timestamp, 0.0 if unknown.

    A float, not the datetime: LangSmith returns tz-aware values but a naive one
    would raise on comparison, and ordering experiments must not be a failure mode.
    """
    try:
        return session.start_time.timestamp()
    except Exception:  # noqa: BLE001 - an ordering hint, never worth a 500
        return 0.0


def _score_from_feedback(stats: dict | None) -> tuple[int, int]:
    """(passed, scored) from an experiment project's `feedback_stats`.

    Summed over every feedback key rather than looking up assistant_evals'
    EVAL_FEEDBACK_KEY, so adding or renaming an evaluator there can't silently zero
    the badge. Scores are 0/1 and mean CORRECT behavior (the polarity note in
    assistant_evals), so `avg * n` rounds back to the number of passing examples.
    """
    passed = scored = 0
    for entry in (stats or {}).values():
        if not isinstance(entry, dict):
            continue
        n = int(entry.get("n") or 0)
        avg = float(entry.get("avg") or 0.0)
        scored += n
        passed += round(avg * n)
    return passed, scored


def _eval_status(workspace: str | None, dataset: str) -> dict:
    """The dataset's latest experiment, read straight from LangSmith. Blocking."""
    # Local knowledge first, so it colours every return path below: a run that died
    # before it created a project is invisible to LangSmith, and a run that started
    # two seconds ago looks identical to no run at all.
    inflight = _inflight_since(dataset)
    with _RUN_LOCK:
        last_error = _LAST_RUN_ERROR.get(dataset, "")
    local: dict = {"last_error": last_error} if last_error else {}

    client = _scoped_client(workspace)
    try:
        ds = client.read_dataset(dataset_name=dataset)
    except LangSmithNotFoundError:
        # NOT-FOUND ONLY, so an outage still reaches the caller as an error: no
        # dataset is an empty state for the panel (assistants created before this
        # feature have none, and setup's dataset creation is best-effort), but a
        # LangSmith 5xx dressed up as "no dataset" would hide a real failure.
        return {**local, "dataset_name": dataset, "exists": False, "running": bool(inflight)}

    examples = int(getattr(ds, "example_count", 0) or 0)
    dataset_url = getattr(ds, "url", None) or ""
    out: dict = {
        **local,
        "dataset_name": dataset,
        "dataset_url": dataset_url,
        "exists": True,
        "example_count": examples,
    }

    # An experiment over a dataset IS a tracing project with that dataset as its
    # reference; `include_stats` is what carries `feedback_stats` (the scores).
    # list_projects makes no ordering promise, so pick the newest ourselves.
    sessions = list(client.list_projects(reference_dataset_id=ds.id, include_stats=True, limit=50))
    latest = max(sessions, key=_started_ts, default=None)
    if latest is None:
        return {**out, "running": bool(inflight), "experiment_name": None, "url": None}

    passed, scored = _score_from_feedback(getattr(latest, "feedback_stats", None))
    started = _started_ts(latest)
    start_time = getattr(latest, "start_time", None)
    return {
        **out,
        "experiment_name": getattr(latest, "name", None),
        # The dataset's compare view, which is where a reviewer wants to land (the
        # SDK's own `session.url` is the bare project page). Falls back to it when
        # LangSmith gave us no dataset URL to hang the query off.
        "url": (
            f"{dataset_url}/compare?selectedSessions={latest.id}"
            if dataset_url
            else getattr(latest, "url", None)
        ),
        "passed": passed,
        "scored": scored,
        # Rows are scored as they finish, so `scored` is the progress counter and
        # `example_count` is what the badge denominator should read.
        "total": examples or scored,
        "started_at": start_time.isoformat() if hasattr(start_time, "isoformat") else None,
        # Either LangSmith shows a part-scored recent experiment, or we know a run is
        # still on a thread here (the first seconds, before its project exists).
        "running": bool(inflight)
        or (scored < examples and 0 < started and time.time() - started < _RUN_STALE_SECS),
    }


def _run_experiment_bg(workspace: str, dataset: str, context: dict, prefix: str) -> None:
    """Thread body: run the experiment to completion. Never raises.

    This runs detached, so an exception has nowhere to go — the request it came from
    was answered a minute earlier. A failed run leaves the previous score standing in
    the panel, which mid-demo is the calm outcome, but it must not leave it standing
    SILENTLY: the run may well have died before `client.evaluate` created anything in
    LangSmith (the target is built first, so a missing Anthropic key or an
    inaccessible workspace raises with no trace to inspect). So the traceback goes to
    the deployment log and the message goes to `_LAST_RUN_ERROR`, which
    `GET /evals/status` hands the panel.
    """
    try:
        # Function-local import (house style, cf. /tools): assistant_evals pulls in
        # agent.py to run the target in-process, and that import is heavy. Resolving
        # at call time is also what lets tests stub the runner on the module.
        from dashboard_agent.assistant_evals import run_experiment

        run_experiment(workspace, dataset, context, experiment_prefix=prefix)
    except Exception as exc:  # noqa: BLE001 - a detached demo run must never crash the server
        traceback.print_exc()
        with _RUN_LOCK:
            _LAST_RUN_ERROR[dataset] = f"{type(exc).__name__}: {exc}"
    finally:
        with _RUN_LOCK:
            _INFLIGHT.pop(dataset, None)


async def evals_run(request):
    """Start the assistant's eval experiment in the background.

    POST {dataset, workspace?, context?, experiment_prefix?} → returns as soon as the
    thread is spawned. Three real agent runs take ~30-90s and the presenter clicks
    this mid-demo, so the request must not wait on it (same fire-and-forget shape as
    the prewarm in assistant_setup). The SPA polls GET /evals/status for the result.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    # `dataset_name` accepted as an alias because that is the key /evals/status
    # hands back, and posting the status object straight back is the obvious thing
    # for a caller to do.
    dataset = (body.get("dataset") or body.get("dataset_name") or "").strip()
    if not dataset:
        return JSONResponse({"error": "dataset is required"}, status_code=400)

    # One run per dataset at a time. A double-click, or the panel open in a second
    # browser, would otherwise spend another three real agent runs and race two
    # experiments into the same dataset. Not an error: the caller wanted a run to be
    # happening, and one is — the panel just keeps polling.
    if _inflight_since(dataset):
        return JSONResponse(
            {"ok": True, "dataset": dataset, "running": True, "already_running": True}
        )
    with _RUN_LOCK:
        _INFLIGHT[dataset] = time.time()
        # A new attempt clears the previous failure, so the panel stops showing an
        # error the presenter is in the middle of retrying.
        _LAST_RUN_ERROR.pop(dataset, None)

    try:
        threading.Thread(
            target=_run_experiment_bg,
            args=(
                body.get("workspace") or "",
                dataset,
                body.get("context") or {},
                body.get("experiment_prefix") or "",
            ),
            daemon=True,
        ).start()
    except Exception as exc:
        with _RUN_LOCK:
            _INFLIGHT.pop(dataset, None)
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)
    # A receipt, not a result: the score arrives via GET /evals/status.
    return JSONResponse({"ok": True, "dataset": dataset, "running": True})


async def evals_status(request):
    """Latest experiment score for an assistant's eval dataset.

    GET ?dataset=<name>[&workspace=<id>] → {dataset_name, dataset_url, exists,
    example_count, running, experiment_name, url, passed, scored, total, started_at,
    last_error}. No `dataset` (or no such dataset in LangSmith) is a calm
    `exists: false`, not an error — assistants created before this feature have none.
    `last_error` is present only when the last run we spawned died; it is about a RUN,
    not about this lookup (which reports its own failure as `error` + a 500).
    """
    dataset = (request.query_params.get("dataset") or "").strip()
    if not dataset:
        return JSONResponse({"dataset_name": "", "exists": False, "running": False})
    try:
        # Two sync LangSmith round trips → off the event loop.
        return JSONResponse(
            await asyncio.to_thread(_eval_status, request.query_params.get("workspace"), dataset)
        )
    except Exception as exc:
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)


# --- sandbox file browser (GET /sandbox-files, GET /sandbox-file) ---------------
#
# The SPA's file dialog reads the assistant's code-execution VM. Everything here is
# read-only and bounded: a wedged VM must not hold an HTTP request, and a 2 GB log
# in /workspace must not travel through the Agent Server process.

# One directory page. deepagents gives no cap of its own, and a pip target dir can
# hold tens of thousands of entries.
_MAX_ENTRIES = 500

# Our own byte cap on a preview page, on top of the VM's ~500 KiB stdout cap — the
# browser is rendering this into a DOM, not grepping it.
_MAX_CONTENT_BYTES = 256 * 1024

# Module-level so tests can shrink it. Applied TWICE per VM call (see `_vm_ls` /
# `_vm_read`): once as the request deadline, once as the in-VM command timeout.
SANDBOX_TIMEOUT = 20

# Ceiling on VM calls in flight from these two routes. `asyncio.to_thread` runs on
# the loop's process-wide default executor (min(32, cpu+4) workers) that
# langgraph_api's own serde/store code also uses, so an unresponsive VM must not be
# able to starve it however many times the user clicks.
_MAX_INFLIGHT = 4
_SANDBOX_SLOTS = asyncio.Semaphore(_MAX_INFLIGHT)

# Extensions we are willing to send through `aread`. This is a SAFETY gate, not a UI
# nicety: deepagents' `_get_file_type` defaults unknown extensions to "text", and the
# in-VM script's text branch falls back to base64-ing the WHOLE file with no size
# guard when the UTF-8 sniff fails. Allowlisting keeps a core dump off that path.
_TEXT_EXTS = frozenset(
    """md markdown mdx txt log csv tsv json jsonl ndjson yaml yml toml ini cfg conf
    py pyi ipynb js jsx ts tsx sh bash sql html htm css scss xml svg rst r rb go rs
    java c h cpp hpp tf mk lock""".split()
)

# Extensionless files worth previewing (the agent writes several of these).
_TEXT_NAMES = frozenset(
    {"dockerfile", "makefile", "readme", "license", "changelog", "agents", "skill"}
)

_MARKDOWN_EXTS = frozenset({"md", "markdown", "mdx"})

# deepagents' in-VM read script emits this literal for a zero-byte file; it would
# render as body text in the viewer.
_EMPTY_FILE_REMINDER = "System reminder: File exists but has empty contents"

# Appended in-VM when the read page blew the sandbox stdout cap (deepagents'
# TRUNCATION_MSG). Matched as a substring so we don't import sandbox internals.
_VM_TRUNCATION_MARKER = "[Output was truncated due to size limits."

_LS_ERROR_STATUS = {"path_not_found": 404, "not_a_directory": 400, "permission_denied": 403}
_READ_ERROR_STATUS = {
    # The read script says "file_not_found"; the SPA branches on one slug for both routes.
    "file_not_found": ("path_not_found", 404),
    "not_a_file": ("not_a_file", 400),
    "permission_denied": ("permission_denied", 403),
}


def _files_root() -> str:
    """Root the browser is confined to (`DA_FILES_ROOT`, default `/workspace`).

    Configurable so the surface can be tightened without a code change.
    """
    load_env()
    root = posixpath.normpath(os.getenv("DA_FILES_ROOT") or "/workspace")
    return root if root.startswith("/") else "/workspace"


def _safe_path(raw: str, root: str) -> str | None:
    """Normalise an absolute path and confine it to `root`, else None.

    `normpath` resolves `..` BEFORE the prefix test, so `/workspace/../etc` is
    rejected. It cannot see symlinks inside the VM — accepted, since the agent can
    already `execute` arbitrary shell there; this only adds a surface, not a power.
    """
    if not raw or "\x00" in raw or not raw.startswith("/"):
        return None
    path = posixpath.normpath(raw)
    if path != root and not path.startswith(root.rstrip("/") + "/"):
        return None
    return path


def _file_kind(name: str) -> str:
    """Classify a file name as previewable ("text") or not ("binary")."""
    low = name.lower()
    # Dotenv files are excluded deliberately: the deployment's only auth is a shared
    # token that ships in the SPA bundle, so a browser-reachable `cat` of any secrets
    # the agent wrote into its VM is a needless key-leak path.
    if low.startswith(".env"):
        return "binary"
    ext = posixpath.splitext(low)[1].lstrip(".")
    if ext:
        return "text" if ext in _TEXT_EXTS else "binary"
    return "text" if low in _TEXT_NAMES else "binary"


def _language(name: str) -> str | None:
    """Viewer hint: "markdown" for md-family files, else the bare extension."""
    ext = posixpath.splitext(name.lower())[1].lstrip(".")
    if ext in _MARKDOWN_EXTS:
        return "markdown"
    return ext or None


def _err(status: int, reason: str, message: str) -> JSONResponse:
    """Error body carrying both a human `error` (what api.ts reads) and a `reason` slug."""
    return JSONResponse({"error": message, "reason": reason}, status_code=status)


def _sandbox_id(backend) -> str | None:
    """The VM name, for the dialog footer. Never worth failing a request over."""
    try:
        return backend.id
    except Exception:  # noqa: BLE001 - a cosmetic field
        return None


def _int_param(request, key: str, default: int) -> int:
    """Query param as an int, falling back to `default` on anything unparseable."""
    try:
        return int(request.query_params.get(key) or default)
    except (TypeError, ValueError):
        return default


async def _resolve_backend(request):
    """(backend, None) for this assistant's VM, or (None, JSONResponse) explaining why not.

    Keyed exactly like the runtime (`agent_repo` → `customer` → "default") so the
    browser sees the SAME VM a chat turn warmed — and, because webapp.py and the
    graph share one process, usually straight out of `_SANDBOX_CACHE` with no network.
    """
    # Function-local ABSOLUTE import (house style, cf. /tools): keeps module load off
    # agent.py's heavy deepagents imports, dodges an import cycle, and — because it
    # resolves at call time — is what lets tests monkeypatch these on the module.
    from dashboard_agent.agent import _ensure_sandbox, _sandbox_enabled, _sandbox_key_from

    load_env()  # `_sandbox_enabled()` reads os.getenv directly and never loads .env itself
    if not _sandbox_enabled():
        return None, _err(
            503, "sandbox_disabled", "Agent file access is turned off for this deployment."
        )
    # assistant_setup writes `ls_artifacts.agent_repo = ""` when there is no Context Hub
    # repo, and the SPA forwards it verbatim → coerce "" to None like the runtime's `or`.
    agent_repo = request.query_params.get("agent_repo") or None
    customer = request.query_params.get("customer") or None
    # attach-only: a toolbar click must never provision a VM (~30s boot + pip install).
    # Sync + network → to_thread so it can't block the event loop.
    backend = await asyncio.to_thread(
        _ensure_sandbox, _sandbox_key_from(agent_repo, customer), create=False
    )
    if backend is None:
        return None, _err(503, "sandbox_unavailable", "No sandbox files for this assistant.")
    return backend, None


def _bounded(backend):
    """A view of `backend` whose IN-VM command timeout is `SANDBOX_TIMEOUT`.

    `asyncio.wait_for` cancels the awaiting coroutine, never the worker thread:
    `als`/`aread` reach the VM via `aexecute` → `asyncio.to_thread(execute)`, and
    `LangSmithSandbox._default_timeout` is 30 MINUTES. Without this, every 504 we
    return would leave a default-executor thread blocked on the sandbox HTTP call
    for half an hour — a pool that is process-wide, ~32 slots, and shared with
    langgraph_api's own `to_thread` callers.

    A shallow copy shares the underlying `Sandbox` object (no new connection, no
    new VM) and leaves the cached backend the agent runs turns on untouched.
    """
    if getattr(backend, "_default_timeout", None) is None:
        return backend  # not a deepagents sandbox backend — nothing to bound
    try:
        clone = copy.copy(backend)
        clone._default_timeout = SANDBOX_TIMEOUT
        return clone
    except Exception:  # noqa: BLE001 - the bound is a safeguard, never a failure mode
        return backend


async def _vm_ls(backend, path: str):
    """One `als` against a time-bounded backend, holding an inflight slot."""
    async with _SANDBOX_SLOTS:
        return await _bounded(backend).als(path)


async def _vm_read(backend, path: str, offset: int, limit: int):
    """One `aread` against a time-bounded backend, holding an inflight slot."""
    async with _SANDBOX_SLOTS:
        return await _bounded(backend).aread(path, offset, limit)


async def sandbox_files(request):
    """List ONE directory of the assistant's sandbox VM (lazy, expand-on-click).

    GET ?path=<abs, default root>&agent_repo=&customer= → {root, path, parent,
    entries[{name, path, is_dir, kind}], truncated, sandbox_id}.

    Lazy rather than recursive: `als` and `aglob` cost the same single VM round trip,
    but `aglob("**/*")` has no depth or entry cap and its output is silently truncated
    mid-JSON-line at the VM's stdout limit — a stray `node_modules` would return half a
    tree with no error flag. One directory per request has an honest bound.
    """
    try:
        root = _files_root()
        path = _safe_path(request.query_params.get("path") or root, root)
        if path is None:
            return _err(400, "invalid_path", f"path must be an absolute path inside {root}")

        backend, failure = await _resolve_backend(request)
        if failure is not None:
            return failure

        try:
            res = await asyncio.wait_for(_vm_ls(backend, path), timeout=SANDBOX_TIMEOUT)
        except TimeoutError:
            return _err(504, "timeout", "The sandbox did not respond.")
        if res.error:
            # deepagents prefixes "Path '<path>': " — match on the suffix code only.
            code = res.error.rsplit(": ", 1)[-1]
            status = _LS_ERROR_STATUS.get(code)
            if status is None:
                return _err(500, "internal", res.error)
            return _err(status, code, res.error)

        entries = []
        for info in res.entries or []:
            p = info.get("path") or ""
            if not p:
                continue
            name = posixpath.basename(p)
            is_dir = bool(info.get("is_dir"))
            # `kind` is extension-derived, so the UI can grey out non-previewable
            # files BEFORE the user clicks (no size/mtime: the scandir script emits
            # only {path, is_dir}).
            entries.append(
                {
                    "name": name,
                    "path": p,
                    "is_dir": is_dir,
                    "kind": "dir" if is_dir else _file_kind(name),
                }
            )
        entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
        return JSONResponse(
            {
                "root": root,
                "path": path,
                "parent": None if path == root else posixpath.dirname(path),
                "entries": entries[:_MAX_ENTRIES],
                "truncated": len(entries) > _MAX_ENTRIES,
                "sandbox_id": _sandbox_id(backend),
            }
        )
    except Exception as exc:
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)


def _placeholder(base: dict, reason: str, message: str) -> JSONResponse:
    """200 for a file that exists but can't be shown — a state, not an error.

    Keeps the viewer's control flow a single switch on `kind` instead of an error path.
    """
    return JSONResponse(
        {
            **base,
            "kind": "binary",
            "language": None,
            "encoding": None,
            "content": None,
            "truncated": False,
            "next_offset": None,
            "reason": reason,
            "message": message,
        }
    )


async def sandbox_file(request):
    """Read ONE file from the assistant's sandbox VM, paginated by line.

    GET ?path=<abs, required>&offset=0&limit=2000&agent_repo=&customer= → a text body
    {kind:"text", language, encoding, content, offset, limit, truncated, next_offset,
    sandbox_id}, or a 200 placeholder {kind:"binary", reason, message, content:null}.

    `truncated` means "this is not the whole file"; `next_offset` is the line to ask
    for next (null on the last page), which is what the viewer's "Show more" sends.
    """
    try:
        root = _files_root()
        raw = request.query_params.get("path")
        if not raw:
            return _err(400, "missing_path", "path is required")
        path = _safe_path(raw, root)
        if path is None:
            return _err(400, "invalid_path", f"path must be an absolute path inside {root}")
        offset = max(0, _int_param(request, "offset", 0))
        limit = min(5000, max(1, _int_param(request, "limit", 2000)))

        backend, failure = await _resolve_backend(request)
        if failure is not None:
            return failure

        name = posixpath.basename(path)
        base = {
            "path": path,
            "name": name,
            "offset": offset,
            "limit": limit,
            "sandbox_id": _sandbox_id(backend),
        }
        if _file_kind(name) != "text":
            label = posixpath.splitext(name)[1] or name
            return _placeholder(base, "binary", f"Binary file ({label}) — preview not supported.")

        try:
            # `aread`, NOT `read`: LangSmithSandbox overrides only the SYNC read, which
            # downloads the ENTIRE file into this process before paginating (a big log
            # would OOM the deployment). `aread` runs the in-VM script — offset/limit
            # applied there, stdout capped at ~500 KiB. Do not "simplify" to to_thread(read).
            #
            # `limit + 1`: the in-VM script stops at `limit` lines WITHOUT flagging
            # it (it sets its own marker only when the ~500 KiB stdout cap blows), so
            # the extra line is the only way to tell "the file ends here" from "the
            # page filled up". It is trimmed off below and never reaches the client.
            res = await asyncio.wait_for(
                _vm_read(backend, path, offset, limit + 1), timeout=SANDBOX_TIMEOUT
            )
        except TimeoutError:
            return _err(504, "timeout", "The sandbox did not respond.")

        if res.error:
            if "exceeds maximum preview size" in res.error:
                return _placeholder(base, "too_large", "File is too large to preview.")
            if "exceeds file length" in res.error:
                return _err(416, "bad_offset", res.error)
            detail = res.error.rsplit(": ", 1)[-1]
            mapped = _READ_ERROR_STATUS.get(detail)
            if mapped is None:
                return _err(500, "internal", res.error)
            return _err(mapped[1], mapped[0], res.error)

        data = res.file_data or {}
        if data.get("encoding") != "utf-8":
            # A text-extension file whose bytes failed the in-VM UTF-8 sniff. Never
            # ship the base64 blob — nothing can render it.
            return _placeholder(
                base, "not_previewable", "Not a UTF-8 text file — preview not supported."
            )

        content = data.get("content") or ""
        if content == _EMPTY_FILE_REMINDER:
            content = ""

        # Three independent caps can cut this page short, and each one has to be
        # reported — a silently-clipped page reads as a complete file. `partial_tail`
        # tracks whether the LAST line survived whole, because a half line must be
        # re-fetched from the start on the next page rather than skipped.
        truncated = False
        partial_tail = False
        if _VM_TRUNCATION_MARKER in content:
            # The VM's stdout cap fired. Drop deepagents' operator-facing suffix: the
            # viewer states this in its own words and offers the next page.
            # rstrip: deepagents' TRUNCATION_MSG opens with "\n\n", which is framing
            # for the message, not two blank lines of the file.
            content = content.split(_VM_TRUNCATION_MARKER, 1)[0].rstrip("\n")
            truncated = partial_tail = True
        lines = content.split("\n") if content else []
        if len(lines) > limit:
            # The `limit + 1`-th line came back, so the file continues past this page.
            lines = lines[:limit]
            content = "\n".join(lines)
            truncated, partial_tail = True, False
        encoded = content.encode("utf-8")
        if len(encoded) > _MAX_CONTENT_BYTES:
            content = encoded[:_MAX_CONTENT_BYTES].decode("utf-8", errors="ignore")
            lines = content.split("\n")
            truncated = partial_tail = True

        whole_lines = max(0, len(lines) - 1) if partial_tail else len(lines)
        return JSONResponse(
            {
                **base,
                "kind": "text",
                "language": _language(name),
                "encoding": "utf-8",
                "content": content,
                "truncated": truncated,
                # Where a "Show more" click resumes. None when this is the last page
                # (or when a single line is itself over a cap, so paging can't advance).
                "next_offset": offset + whole_lines if truncated and whole_lines else None,
            }
        )
    except Exception as exc:
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)


# --- demo traffic --------------------------------------------------------------

# The "one backfill per project" guard and the receipt live in demo_traffic, not here,
# because the automatic backfill at assistant creation does not come through this route
# (see the registry comment there). Reading them from that module is what makes the two
# paths interlock: this route refuses to start a second backfill over setup's, and the
# status route below reports setup's while it runs.


async def demo_traffic(request):
    """Backfill a day of synthetic traffic into an assistant's trace project.

    POST {project, workspace?, context?, actions?, data_gap?, customer?} → a receipt.
    Slow (real seed runs + a few thousand run ingests), so `start_demo_traffic` puts it
    on a thread. Exists mainly so assistants created BEFORE the automatic backfill can
    be given traffic without recreating them.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    project = (body.get("project") or body.get("ls_project") or "").strip()
    if not project:
        return JSONResponse({"error": "project is required"}, status_code=400)

    from dashboard_agent.demo_traffic import start_demo_traffic

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


# Tabs hanging off a tracing project's URL. They are `?tab=<n>` query params on the
# project page — Insights is 3, Engine is 4 — NOT path segments: `<project>/insights`
# is not a route, so both deep links used to land on a broken page. Undocumented UI
# internals, so they stay isolated here: if the indices move this is the only line to
# change, and the fallback is the project page itself, which always works.
_PROJECT_TABS = {"insights": 3, "engine": 4}


def _project_links(workspace: str | None, project: str) -> dict:
    """LangSmith URLs for a trace project and its Insights/Engine tabs.

    Blocking (one read_project round trip) — call it off the event loop.
    """
    session = _scoped_client(workspace).read_project(project_name=project)
    base = str(getattr(session, "url", "") or "").rstrip("/")
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
    from dashboard_agent.demo_traffic import SYNTHETIC_TAG

    stats = _scoped_client(workspace).get_run_stats(
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

    from dashboard_agent.demo_traffic import demo_traffic_state

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


app = Starlette(
    routes=[
        Route("/feedback", feedback, methods=["POST"]),
        Route("/tools", tools, methods=["GET"]),
        Route("/sandbox-files", sandbox_files, methods=["GET"]),
        Route("/sandbox-file", sandbox_file, methods=["GET"]),
        Route("/projects", projects, methods=["GET", "POST"]),
        Route("/workspaces", workspaces, methods=["GET"]),
        Route("/hub-prompts", hub_prompts, methods=["GET"]),
        Route("/agents", agents, methods=["GET"]),
        Route("/cleanup", cleanup, methods=["POST"]),
        Route("/trace-url", trace_url, methods=["GET"]),
        Route("/evals/run", evals_run, methods=["POST"]),
        Route("/evals/status", evals_status, methods=["GET"]),
        Route("/demo-traffic", demo_traffic, methods=["POST"]),
        Route("/demo-traffic/status", demo_traffic_status, methods=["GET"]),
    ]
)
