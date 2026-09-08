"""Per-assistant evals (`POST /evals/run`, `GET /evals/status`).

Setup provisions a LangSmith dataset per assistant (see provisioning/evals.py); these two
routes are the SPA's handle on it — start an experiment, read the latest score.
LangSmith is the SOURCE OF TRUTH: /evals/status re-derives the score from the
dataset's experiments on every call, so the panel survives a page reload, a second
browser, and a redeploy mid-demo. The only state kept here is what LangSmith cannot
know — that a run is on a thread in THIS process, and why the last one died (see
_INFLIGHT / _LAST_RUN_ERROR); nothing correct depends on either surviving.
"""

from __future__ import annotations

import asyncio
import threading
import time
import traceback

from langsmith.utils import LangSmithNotFoundError
from starlette.responses import JSONResponse

from custom_demo.config import scoped_client
from custom_demo.provisioning.evals import run_experiment
from custom_demo.web.errors import route_error

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

    Summed over every feedback key rather than looking up provisioning/evals.py's
    EVAL_FEEDBACK_KEY, so adding or renaming an evaluator there can't silently zero
    the badge. Scores are 0/1 and mean CORRECT behavior (the polarity note in
    provisioning/evals.py), so `avg * n` rounds back to the number of passing examples.
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


def _experiment_summary(
    latest, out: dict, dataset_url: str, examples: int, inflight: float
) -> dict:
    """The status body for a dataset that HAS been run at least once."""
    passed, scored = _score_from_feedback(latest.feedback_stats)
    started = _started_ts(latest)
    start_time = latest.start_time
    return {
        **out,
        "experiment_name": latest.name,
        # The dataset's compare view, which is where a reviewer wants to land (the
        # SDK's own `session.url` is the bare project page). Falls back to it when
        # LangSmith gave us no dataset URL to hang the query off.
        "url": (
            f"{dataset_url}/compare?selectedSessions={latest.id}" if dataset_url else latest.url
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


def _eval_status(workspace: str | None, dataset: str) -> dict:
    """The dataset's latest experiment, read straight from LangSmith. Blocking."""
    # Local knowledge first, so it colours every return path below: a run that died
    # before it created a project is invisible to LangSmith, and a run that started
    # two seconds ago looks identical to no run at all.
    inflight = _inflight_since(dataset)
    with _RUN_LOCK:
        last_error = _LAST_RUN_ERROR.get(dataset, "")

    local: dict = {"last_error": last_error} if last_error else {}

    client = scoped_client(workspace)
    try:
        ds = client.read_dataset(dataset_name=dataset)
    except LangSmithNotFoundError:
        # NOT-FOUND ONLY, so an outage still reaches the caller as an error: no
        # dataset is an empty state for the panel (assistants created before this
        # feature have none, and setup's dataset creation is best-effort), but a
        # LangSmith 5xx dressed up as "no dataset" would hide a real failure.
        return {**local, "dataset_name": dataset, "exists": False, "running": bool(inflight)}

    examples = int(ds.example_count or 0)
    dataset_url = ds.url or ""
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

    return _experiment_summary(latest, out, dataset_url, examples, inflight)


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
    the prewarm in provisioning/setup.py). The SPA polls GET /evals/status for the result.
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - an unreadable body is a client error; answer 400, not 500
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
    except Exception as exc:  # noqa: BLE001 - a thread that won't start is reported, and the inflight lock released
        with _RUN_LOCK:
            _INFLIGHT.pop(dataset, None)

        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)

    # A receipt, not a result: the score arrives via GET /evals/status.
    return JSONResponse({"ok": True, "dataset": dataset, "running": True})


@route_error
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

    # Two sync LangSmith round trips → off the event loop.
    return JSONResponse(
        await asyncio.to_thread(_eval_status, request.query_params.get("workspace"), dataset)
    )
