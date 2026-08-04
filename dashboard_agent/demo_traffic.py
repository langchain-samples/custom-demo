"""Synthetic demo traffic: backfill a day of realistic traces into an assistant's project.

A freshly-created assistant's LangSmith project holds whatever the presenter clicked —
three or four traces — so every chart in Monitoring is empty or a single spike, and
Insights has nothing to cluster. This module fills that project with a day of traffic
so those views have something to show.

The trick that makes it cheap: runs are ingested with BACKDATED timestamps, so "a drip
of traffic over a day" and "a few hundred traces written in one batch, timestamped
across that day" render identically in every monitoring view. One batch at creation and
the history exists — no drip thread, no on/off toggle, no expiry, and nothing that has
to survive the deployment scaling to zero.

How far back you can go is capped by the server, not by us: see MAX_BACKDATE_HOURS.
That cap is the reason this is a dense day rather than the week it was first scoped as.

Three things are load-bearing:

1. **We replay, we do not fabricate.** A real trace of this agent is 50-151 runs at
   depth 11 — every model call wrapped in nine middleware chain runs. Hand-building
   that would look fake in the trace view and would drift with every deepagents
   upgrade. Instead we run the agent for real a handful of times, then clone those
   traces with shifted times and fresh ids. `shift_trace` is the same algorithm as
   `RunTree._remap_for_project` (langsmith `run_trees.py:717`), plus a time shift.
2. **The failure mode has to be visible.** `GAP_SHARE` of the backfill replays the gap
   probe — the run where the agent confidently invents figures for the withheld topic —
   carrying `failure_mode` and `data_gap` metadata and mostly-negative feedback. That
   recurring pattern is what Insights clusters on; without it there is nothing to find.
3. **Synthetic runs are marked.** Every emitted run carries `metadata.synthetic = true`
   and the `synthetic-demo` tag. This data lands in a project that also holds real
   traces, and anyone reading those charts later - or any monitor built on the project -
   has to be able to tell the two apart.

Teardown is free: these runs live in the assistant's own trace project, which is already
`metadata.ls_artifacts.project` and already deleted by `POST /cleanup`.
"""

from __future__ import annotations

import datetime as dt
import random
import uuid
from typing import Any

from langsmith.uuid import uuid7_from_datetime

# `assistant_setup` imports THIS module lazily (inside prepare_assistant), so the
# dependency only runs one way at import time and there is no cycle.
from .assistant_setup import _ws_client

# --- shape of a backfill -------------------------------------------------------

# THE constraint on this whole feature, found empirically (the SDK does not enforce it
# and the docs do not mention it): the ingest API rejects any run whose start_time is
# more than 24 hours from now, with
#     "start_time for post must be within ±24 hours of current time".
# `create_run` raises it; `multipart_ingest`/`batch_ingest_runs` only LOG it and drop
# the batch, which is why this has to be respected here rather than discovered at
# runtime. There is no bulk-import endpoint to work around it (bulk-exports is
# export-only). So a backfill is a dense DAY of traffic, not a week.
MAX_BACKDATE_HOURS = 24

# Stay clear of the boundary: a trace scheduled at exactly -24h would be rejected by
# the time the batch is built and sent.
DEFAULT_HOURS = 23

# Dense enough that every hour of the window has several traces, so Monitoring's
# per-hour buckets are populated rather than sparse.
DEFAULT_COUNT = 240

# Share of backfilled traces that replay the gap probe (the fabricating run). High
# enough that Insights forms a cluster rather than treating it as noise, low enough
# that the assistant still looks mostly healthy.
GAP_SHARE = 0.2

# Share given a synthetic error, so the error-rate chart is not a flat zero.
ERROR_SHARE = 0.05

# Tag + metadata flag on every emitted run. See the module docstring: this is how a
# reader tells demo data from real traffic.
SYNTHETIC_TAG = "synthetic-demo"

# Hourly weights for a plausible business-day shape (index = UTC hour). Traffic is
# quiet overnight and peaks late morning / early afternoon. Without this the scatter
# is uniform and the per-hour chart reads as obviously generated.
_HOUR_WEIGHTS = (
    0.2, 0.15, 0.1, 0.1, 0.1, 0.15, 0.3, 0.6, 1.0, 1.6, 2.0, 2.1,
    1.9, 2.0, 2.1, 1.8, 1.4, 1.0, 0.7, 0.5, 0.4, 0.35, 0.3, 0.25,
)  # fmt: skip

# A small pool of end users, so the Threads view and any per-user grouping populate
# instead of showing one user with 280 threads.
_USER_POOL = 12

# Errors we inject, chosen to look like things that actually go wrong in this agent
# rather than generic exception text.
_ERRORS = (
    "APITimeoutError: Request timed out after 120.0s",
    "RateLimitError: 429 rate_limit_error - number of concurrent connections exceeded",
    "ToolException: datasearch returned no rows for the requested topic",
    "APIStatusError: 529 overloaded_error - Anthropic API temporarily overloaded",
)

# The dotted_order timestamp format (langsmith run_trees.py:1342). Exactly six
# microsecond digits — the parser slices a fixed width, so %f must not be truncated.
_DO_TS = "%Y%m%dT%H%M%S%fZ"


def _seg(ts: dt.datetime, run_id: uuid.UUID) -> str:
    """One dotted_order segment: timestamp prefix + the run's uuid."""
    return ts.strftime(_DO_TS) + str(run_id)


def _as_dt(value: Any) -> dt.datetime | None:
    """Coerce a LangSmith timestamp (datetime or ISO string) to an aware datetime."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.UTC)
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def _jitter_usage(usage: dict, scale: float) -> dict:
    """Scale token counts by `scale`, keeping only keys LangSmith accepts.

    `validate_extracted_usage_metadata` (run_trees.py:284) rejects anything outside its
    allowed set, and a rejected dict takes the whole batch down — so we copy across a
    known-good subset rather than passing the original through.
    """
    out: dict[str, Any] = {}
    for key in ("input_tokens", "output_tokens"):
        val = usage.get(key)
        if isinstance(val, (int, float)):
            out[key] = max(1, int(val * scale))
    if "input_tokens" in out or "output_tokens" in out:
        out["total_tokens"] = out.get("input_tokens", 0) + out.get("output_tokens", 0)
    for key in ("input_token_details", "output_token_details"):
        details = usage.get(key)
        if isinstance(details, dict):
            scaled = {
                k: max(0, int(v * scale)) for k, v in details.items() if isinstance(v, (int, float))
            }
            if scaled:
                out[key] = scaled
    # Cache reads can't exceed the (scaled) input token count, and a nonsensical split
    # makes the cost breakdown in the UI look wrong.
    detail = out.get("input_token_details")
    if isinstance(detail, dict) and "input_tokens" in out:
        for k in ("cache_read", "cache_creation"):
            if k in detail:
                detail[k] = min(detail[k], out["input_tokens"])
    return out


def fetch_trace(client: Any, project: str, trace_id: Any) -> list[Any]:
    """Every run in one trace, ordered root-first by dotted_order.

    `list_runs`' default select already carries `extra` (metadata + invocation_params),
    `inputs`/`outputs` and the usage fields, so the result is enough to replay from.
    """
    runs = list(client.list_runs(project_name=project, trace_id=trace_id))
    return sorted(runs, key=lambda r: str(getattr(r, "dotted_order", "") or ""))


def shift_trace(
    runs: list[Any],
    new_start: dt.datetime,
    *,
    project: str,
    rng: random.Random,
    duration_scale: float = 1.0,
    token_scale: float = 1.0,
    thread_id: str = "",
    user_id: str = "",
    error: str = "",
    extra_metadata: dict | None = None,
) -> list[dict]:
    """Clone `runs` as a new trace starting at `new_start`. Returns ingest-ready dicts.

    Same remap as `RunTree._remap_for_project` (run_trees.py:752) — fresh ids, rebuilt
    dotted_order — with the timestamps moved too.

    `duration_scale` is applied to the WHOLE trace rather than per run: offsets and
    durations scale together, so a child can never escape its parent's window and the
    waterfall stays coherent. Per-run jitter would tear the nesting apart.
    """
    if not runs:
        return []
    root = runs[0]
    origin = _as_dt(getattr(root, "start_time", None))
    if origin is None:
        return []

    def moved(ts: Any) -> dt.datetime | None:
        """Same point in the trace's timeline, rebased on new_start and scaled."""
        val = _as_dt(ts)
        if val is None:
            return None
        return new_start + (val - origin) * duration_scale

    ids: dict[str, uuid.UUID] = {}
    orders: dict[str, str] = {}
    out: list[dict] = []
    root_id: uuid.UUID | None = None
    # Runs arrive sorted by dotted_order, so a parent is always rebuilt before its
    # children and `orders[parent]` is populated by the time a child needs it.
    for run in runs:
        old_id = str(getattr(run, "id", "") or "")
        start = moved(getattr(run, "start_time", None))
        if not old_id or start is None:
            continue
        new_id = uuid7_from_datetime(start)  # uuid7 embeds the backdated time, as real runs do
        ids[old_id] = new_id
        if root_id is None:
            root_id = new_id

        old_parent = getattr(run, "parent_run_id", None)
        parent_key = str(old_parent) if old_parent else ""
        parent_order = orders.get(parent_key, "")
        order = f"{parent_order}.{_seg(start, new_id)}" if parent_order else _seg(start, new_id)
        orders[old_id] = order

        end = moved(getattr(run, "end_time", None)) or start + dt.timedelta(milliseconds=50)

        extra = dict(getattr(run, "extra", None) or {})
        meta = dict(extra.get("metadata") or {})
        usage = meta.get("usage_metadata")
        if isinstance(usage, dict):
            meta["usage_metadata"] = _jitter_usage(usage, token_scale)
        meta["synthetic"] = True
        if thread_id:
            meta["thread_id"] = thread_id
        if user_id:
            meta["user_id"] = user_id
        if extra_metadata:
            meta.update(extra_metadata)
        extra["metadata"] = meta

        tags = [t for t in (getattr(run, "tags", None) or []) if t != SYNTHETIC_TAG]
        payload: dict[str, Any] = {
            "id": str(new_id),
            "trace_id": str(root_id),
            "dotted_order": order,
            "session_name": project,
            "name": getattr(run, "name", "") or "run",
            "run_type": getattr(run, "run_type", "chain") or "chain",
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "inputs": getattr(run, "inputs", None) or {},
            "outputs": getattr(run, "outputs", None) or {},
            "extra": extra,
            "tags": [*tags, SYNTHETIC_TAG],
        }
        if parent_key and parent_key in ids:
            payload["parent_run_id"] = str(ids[parent_key])
        # `serialized` is dropped by the client for anything but llm/prompt runs
        # (client.py:2328), so only carry it where it survives.
        if payload["run_type"] in ("llm", "prompt"):
            serialized = getattr(run, "serialized", None)
            if serialized:
                payload["serialized"] = serialized
        out.append(payload)

    if error and out:
        _apply_error(out, error, rng)
    return out


def _apply_error(runs: list[dict], error: str, rng: random.Random) -> None:
    """Fail one leaf run and propagate the error up its ancestor chain.

    A run that errors in isolation is not what a real failure looks like: the exception
    surfaces through every parent up to the root, which is what makes the trace show as
    errored in the UI and count toward the error rate.
    """
    leaves = [r for r in runs if r["run_type"] in ("llm", "tool")] or runs
    target = rng.choice(leaves)
    chain = target["dotted_order"]
    for run in runs:
        # An ancestor's dotted_order is a prefix of the failing run's.
        if chain == run["dotted_order"] or chain.startswith(run["dotted_order"] + "."):
            run["error"] = error
            run["outputs"] = {}


def _schedule(
    hours: int, count: int, rng: random.Random, *, now: dt.datetime | None = None
) -> list[dt.datetime]:
    """`count` timestamps across the last `hours`, weighted by hour of day. Ascending.

    Capped at MAX_BACKDATE_HOURS — anything older is silently dropped by the ingest
    API, so generating it would just be work that vanishes.
    """
    end = now or dt.datetime.now(dt.UTC)
    span = min(hours, MAX_BACKDATE_HOURS)
    oldest = end - dt.timedelta(hours=span)
    # Weight each whole hour in the window by its UTC hour-of-day, so the scatter has
    # a believable daily rhythm instead of being uniform.
    slots = [oldest + dt.timedelta(hours=i) for i in range(span)]
    weights = [_HOUR_WEIGHTS[s.hour] for s in slots]
    stamps: list[dt.datetime] = []
    for slot in rng.choices(slots, weights=weights, k=max(1, count)):
        when = slot + dt.timedelta(minutes=rng.randrange(60), seconds=rng.randrange(60))
        if oldest <= when < end:
            stamps.append(when)
    return sorted(stamps)


# --- seeding (real runs) -------------------------------------------------------


def seed_questions(actions: list[dict] | None, data_gap: str = "") -> list[dict]:
    """The questions to run for real, as [{question, is_gap}].

    Drawn from the assistant's own quick actions so the replayed traffic asks what
    this customer's users would ask. The gap probe is kept separate because the
    backfill needs to over-represent it (see GAP_SHARE) — it is the failure the
    monitoring demo is about.
    """
    out: list[dict] = []
    for action in actions or []:
        question = (action or {}).get("question")
        if not question:
            continue
        out.append({"question": question, "is_gap": (action or {}).get("kind") == "gap"})
    # A gap-planting assistant whose actions predate the `kind` tag: fall back to the
    # last action, which is where `prepare_assistant` puts the probe.
    if data_gap and out and not any(a["is_gap"] for a in out):
        out[-1]["is_gap"] = True
    return out


def run_seeds(context: dict | None, questions: list[dict], *, project: str = "") -> list[dict]:
    """Run the agent for real, once per question. Blocking and expensive (~$0.05 each).

    These runs ARE traffic — they trace into the assistant's project like any other
    run — and they double as the replay seeds, so nothing is paid for twice. Returns
    [{trace_id, is_gap}] for the ones that produced a trace.
    """
    from langchain_core.tracers.context import collect_runs
    from langsmith import tracing_context

    from .assistant_evals import make_run_context

    ctx = make_run_context(context)
    out: list[dict] = []
    agent = None
    for item in questions:
        try:
            if agent is None:  # built lazily so a bad context fails one question, not all
                from .agent import build_agent

                agent = build_agent()
            # `collect_runs` captures the root run synchronously as it completes.
            # `get_current_run_tree()` does NOT work here: it reads a context var that
            # is only set INSIDE a traced call, so after `invoke` returns it is always
            # None and every seed is silently discarded.
            with (
                tracing_context(enabled=True, project_name=project or None),
                collect_runs() as collected,
            ):
                agent.invoke(
                    {"messages": [{"role": "user", "content": item["question"]}]},
                    config={"configurable": {"thread_id": str(uuid.uuid4())}},
                    context=ctx,
                )
            traced = getattr(collected, "traced_runs", None) or []
            trace_id = str(getattr(traced[0], "id", "") or "") if traced else ""
            if trace_id:
                out.append({"trace_id": trace_id, "is_gap": item.get("is_gap", False)})
        except Exception:  # noqa: BLE001 - one bad seed must not lose the others
            continue
    return out


# --- backfill ------------------------------------------------------------------


def backfill(
    client: Any,
    project: str,
    seeds: list[dict],
    *,
    hours: int = DEFAULT_HOURS,
    count: int = DEFAULT_COUNT,
    gap_share: float = GAP_SHARE,
    error_share: float = ERROR_SHARE,
    rng: random.Random | None = None,
    chunk: int = 400,
) -> dict:
    """Clone `seeds` across the last `hours` as `count` traces. Returns a summary.

    `seeds` is [{trace_id, is_gap, runs}] — `runs` fetched once and reused, since the
    same handful of traces is replayed hundreds of times.
    """
    rng = rng or random.Random()
    pool = [s for s in seeds if s.get("runs")]
    if not pool:
        return {"traces": 0, "runs": 0, "error": "no seed traces"}
    gaps = [s for s in pool if s.get("is_gap")]
    normal = [s for s in pool if not s.get("is_gap")] or pool

    stamps = _schedule(hours, count, rng)
    batch: list[dict] = []
    emitted = total_runs = gap_count = error_count = 0
    feedback: list[tuple[str, int, bool]] = []  # (run_id, score, is_gap)

    def flush() -> None:
        nonlocal batch
        if batch:
            client.multipart_ingest(create=batch)
            batch = []

    for when in stamps:
        is_gap = bool(gaps) and rng.random() < gap_share
        seed = rng.choice(gaps if is_gap else normal)
        errored = rng.random() < error_share
        runs = shift_trace(
            seed["runs"],
            when,
            project=project,
            rng=rng,
            # Latency and token counts need spread or every chart is a flat line.
            duration_scale=rng.uniform(0.7, 1.4),
            token_scale=rng.uniform(0.85, 1.2),
            thread_id=str(uuid.uuid4()),
            user_id=f"demo-user-{rng.randrange(_USER_POOL):02d}",
            error=rng.choice(_ERRORS) if errored else "",
            # Mark the gap replays explicitly. `failure_mode` can't do this job: it is
            # inherited from the seed and is set on EVERY trace of a hallucination
            # assistant, so it says what the assistant IS, not what this run DID.
            # This flag is what a presenter (and an Insights filter) selects on.
            extra_metadata={"demo_gap_probe": is_gap},
        )
        if not runs:
            continue
        emitted += 1
        total_runs += len(runs)
        gap_count += int(is_gap)
        error_count += int(errored)
        # Thumbs skew negative on the fabricating runs and positive elsewhere — that
        # correlation is what makes the failure legible in aggregate, and it is what
        # an Insights attribute like `user_satisfied` keys off.
        if not errored and rng.random() < 0.35:
            score = 0 if (is_gap and rng.random() < 0.8) else 1
            feedback.append((runs[0]["id"], score, is_gap))
        batch.extend(runs)
        if len(batch) >= chunk:
            flush()
    flush()

    for run_id, score, is_gap in feedback:
        try:
            client.create_feedback(
                run_id=run_id,
                key="user_score",
                score=score,
                comment=("Made up numbers — we don't track that." if not score and is_gap else ""),
            )
        except Exception:  # noqa: BLE001 - feedback is a garnish, not the payload
            continue
    return {
        "traces": emitted,
        "runs": total_runs,
        "gap_traces": gap_count,
        "errored_traces": error_count,
        "feedback": len(feedback),
        "hours": min(hours, MAX_BACKDATE_HOURS),
    }


# --- insights ------------------------------------------------------------------


def ensure_insights_job(
    client: Any, project: str, *, customer: str = "", data_gap: str = "", run: bool = True
) -> dict:
    """Create an Insights config on `project` steered at the failure mode, and run it.

    Insights is fully API-driven (`POST /sessions/{id}/insights/configs`, then
    `POST /sessions/{id}/insights`), so the cluster can already be computed by the time
    the presenter opens the tab. `attribute_schemas` is what makes the fabrication
    legible: extracting `fabricated_figures` per trace pushes the clustering to split
    the invented-numbers runs out rather than lumping them in with normal analytics
    questions.
    """
    session_id = str(client.read_project(project_name=project).id)
    topic = data_gap or "a topic the assistant has no data for"
    config = {
        "name": "Demo: answer quality",
        "config": {
            "summary_prompt": (
                f"Summarize what the user asked {customer or 'this'} assistant for and how well "
                f"it answered. Call out explicitly when the assistant presented specific figures "
                f"as fact for topics it has no data on — especially '{topic}' — versus when it "
                f"correctly said the data was unavailable."
            ),
            "attribute_schemas": {
                "fabricated_figures": {
                    "type": "boolean",
                    "description": (
                        "True if the answer states specific numbers as established fact without "
                        "them being present in the retrieved data."
                    ),
                },
                "user_satisfied": {
                    "type": "boolean",
                    "description": "True if the user appears satisfied with the answer.",
                },
            },
            "last_n_hours": MAX_BACKDATE_HOURS,
            "model": "anthropic",
        },
    }
    created = client.request_with_retries(
        "POST", f"/sessions/{session_id}/insights/configs", json=config
    ).json()
    out: dict[str, Any] = {"session_id": session_id, "config_id": created.get("id", "")}
    if not (run and out["config_id"]):
        return out
    try:
        job = client.request_with_retries(
            "POST",
            f"/sessions/{session_id}/insights",
            json={"config_id": out["config_id"], "last_n_hours": MAX_BACKDATE_HOURS},
        ).json()
        out["job_id"] = job.get("id", "")
        out["status"] = job.get("status", "")
    except Exception as exc:  # noqa: BLE001
        # Running a job needs a model secret on the WORKSPACE (Settings -> Model
        # secrets); the API answers a bare 422 listing the missing key, e.g.
        # {"detail": "['ANTHROPIC_API_KEY']"}. The config we just saved is still
        # good and the presenter can hit Run in the UI, so translate rather than
        # raise — this is the likeliest failure on a fresh customer workspace.
        detail = str(exc)
        out["job_error"] = (
            "insights config saved, but the job needs a model secret in this "
            "workspace (Settings -> Model secrets): " + detail[:200]
            if "API_KEY" in detail
            else f"insights job failed: {detail[:200]}"
        )
    return out


# --- entry point ---------------------------------------------------------------


def generate_demo_traffic(
    workspace: str,
    project: str,
    *,
    context: dict | None = None,
    actions: list[dict] | None = None,
    data_gap: str = "",
    customer: str = "",
    hours: int = DEFAULT_HOURS,
    count: int = DEFAULT_COUNT,
    seed_traces: list[dict] | None = None,
    with_insights: bool = True,
) -> dict:
    """Seed real runs, backfill a day of synthetic traffic, and kick an Insights job.

    Best-effort by contract: every failure is swallowed and reported in the return
    value. Called from a daemon thread at assistant creation and from
    `POST /demo-traffic`, and neither may fail because LangSmith had a bad minute.

    Blocking and slow — the seed phase is several real agent runs (~2 min, ~$0.25).
    Pass `seed_traces` to skip it and replay traces that already exist.
    """
    result: dict[str, Any] = {"project": project, "seeded": 0}
    try:
        client = _ws_client(workspace)
        seeds = list(seed_traces or [])
        if not seeds:
            seeds = run_seeds(context, seed_questions(actions, data_gap), project=project)
            result["seeded"] = len(seeds)
        if not seeds:
            result["error"] = "no seed traces produced"
            return result
        for seed in seeds:
            seed["runs"] = fetch_trace(client, project, seed["trace_id"])
        result.update(backfill(client, project, seeds, hours=hours, count=count))
    except Exception as exc:  # noqa: BLE001 - never break the caller
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    if with_insights:
        try:
            result["insights"] = ensure_insights_job(
                client, project, customer=customer, data_gap=data_gap
            )
        except Exception as exc:  # noqa: BLE001 - the traffic is the payload; insights is extra
            result["insights_error"] = f"{type(exc).__name__}: {exc}"
    return result
