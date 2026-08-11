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
import threading
import time
import traceback
import uuid
from typing import Any, cast

from langsmith.uuid import uuid7_from_datetime

# `assistant_setup` imports THIS module lazily (inside prepare_assistant), so the
# dependency only runs one way at import time and there is no cycle.
from .assistant_setup import _ws_client, playground_model_id

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


def fetch_trace(
    client: Any, project: str, trace_id: Any, *, attempts: int = 20, delay: float = 4.0
) -> list[Any]:
    """Every run in one trace, ordered root-first by dotted_order.

    `list_runs`' default select already carries `extra` (metadata + invocation_params),
    `inputs`/`outputs` and the usage fields, so the result is enough to replay from.

    Polls until the run count STOPS GROWING, because a seed trace is read seconds after
    the run that produced it and arrives in pieces: the tracer uploads on a background
    thread, the server indexes after that, and a 50-151 run trace becomes visible a
    batch at a time. Measured against a live workspace, even a one-run trace posted
    directly takes ~10s to become queryable — so an accept-on-first-hit read either
    finds nothing (the whole backfill then dies as "no seed traces", having already
    paid for the runs) or clones a root with half its children into 240 thin traces.
    Two identical non-empty reads means the trace has settled.

    The wait is bounded at `attempts * delay` and costs nothing on the happy path: this
    runs on the backfill's daemon thread, behind several minutes of real agent turns.
    """
    runs: list[Any] = []
    seen = 0
    for attempt in range(attempts):
        runs = list(client.list_runs(project_name=project, trace_id=trace_id))
        if runs and len(runs) == seen:
            break
        seen = len(runs)
        if attempt < attempts - 1:
            time.sleep(delay)
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


def collected_trace_id(traced_runs: list[Any]) -> str:
    """The id of the trace a `collect_runs()` block produced: its OUTERMOST run.

    Not `traced_runs[0]`, and not `.trace_id`. Measured against a real run of this
    agent, `collect_runs` hands back ~11 objects, each of which looks like a root
    locally — `parent_run_id is None`, `trace_id == id`, self-referential
    `dotted_order` — because LangGraph runs tools and model calls under fresh callback
    managers that never saw the parent. Only ONE of them is a root server-side (the
    `LangGraph` chain run); the rest are reconciled into its trace on ingest and their
    local ids exist nowhere, so `list_runs(trace_id=...)` on one returns nothing no
    matter how long you wait. That is what emptied every backfill: the list is ordered
    by COMPLETION, so [0] was the first `ChatAnthropic` call to return.

    The outermost run is the one that STARTED first — a child cannot begin before its
    parent — which holds for a flat single-run trace too.
    """
    if not traced_runs:
        return ""
    far_future = dt.datetime.max.replace(tzinfo=dt.UTC)
    outermost = min(traced_runs, key=lambda r: _as_dt(getattr(r, "start_time", None)) or far_future)
    return str(getattr(outermost, "id", "") or "")


def run_seeds(
    context: dict | None, questions: list[dict], *, project: str = "", client: Any = None
) -> list[dict]:
    """Run the agent for real, once per question. Blocking and expensive (~$0.05 each).

    These runs ARE traffic — they trace into the assistant's project like any other
    run — and they double as the replay seeds, so nothing is paid for twice. Returns
    [{trace_id, is_gap}] for the ones that produced a trace.

    `client` MUST be the same workspace-scoped client the backfill reads with. A
    LangSmith key selects a workspace, and `tracing_context` without a client builds
    the tracer from the ambient env key — so for a customer in another workspace the
    seed runs land in a same-named project over THERE, `fetch_trace` finds nothing in
    the real project, and the backfill dies with "no seed traces" having already paid
    for the runs. `graph.py` routes per-run traces the same way, for the same reason.
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
            # `collect_runs` captures the runs of this call synchronously as they
            # complete (see `collected_trace_id` for which one to read).
            # `get_current_run_tree()` does NOT work here: it reads a context var that
            # is only set INSIDE a traced call, so after `invoke` returns it is always
            # None and every seed is silently discarded.
            with (
                tracing_context(enabled=True, client=client, project_name=project or None),
                collect_runs() as collected,
            ):
                agent.invoke(
                    {"messages": [{"role": "user", "content": item["question"]}]},
                    config={"configurable": {"thread_id": str(uuid.uuid4())}},
                    context=ctx,
                )
            trace_id = collected_trace_id(getattr(collected, "traced_runs", None) or [])
            if trace_id:
                out.append({"trace_id": trace_id, "is_gap": item.get("is_gap", False)})
        except Exception:  # noqa: BLE001 - one bad seed must not lose the others
            # Printed because the caller only ever sees a count: a seed that raises
            # every time (bad model key, bad context) otherwise reads downstream as the
            # same bare "no seed traces" as a trace that merely wasn't visible yet.
            traceback.print_exc()
            continue
    # The tracer uploads on a background thread, so without this the caller can start
    # reading the traces back before they have been sent at all.
    if client is not None:
        try:
            client.flush()
        except Exception:  # noqa: BLE001 - a failed flush only costs us the retries below
            traceback.print_exc()
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
    # Root ids to offer the annotation queue, collected here because this is the only
    # place that knows which trace was a gap probe and which one errored. Errored traces
    # are skipped: "did the assistant invent figures" is unanswerable for a run that
    # never produced an answer.
    reviewable: list[dict] = []

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
        if not errored:
            reviewable.append({"run_id": runs[0]["id"], "is_gap": is_gap})
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
        "reviewable": _review_sample(reviewable, rng),
    }


def _review_sample(reviewable: list[dict], rng: random.Random) -> list[dict]:
    """Pick the traces to queue for human review: mostly fabrications, some clean.

    A queue of nothing but gap probes teaches an annotator to click one button, and a
    random sample of 10 out of a 20%-gap backfill would often contain one or none. So
    the split is deliberate — enough fabrications to see the pattern, enough grounded
    answers that the rubric has to be read. Trimmed to `QUEUE_SIZE` here rather than
    returned whole: this list travels in the backfill receipt.
    """
    gaps = [r for r in reviewable if r["is_gap"]]
    clean = [r for r in reviewable if not r["is_gap"]]
    rng.shuffle(gaps)
    rng.shuffle(clean)
    want_gaps = min(len(gaps), round(QUEUE_SIZE * QUEUE_GAP_SHARE))
    picked = gaps[:want_gaps] + clean[: QUEUE_SIZE - want_gaps]
    # Backfill order is chronological; interleave so the queue is not "6 bad then 4 good".
    rng.shuffle(picked)
    return picked


# --- insights ------------------------------------------------------------------

# Share of the project's runs Insights reads, and the run filter — both the values the
# UI writes when you save a config. `eq(is_root, true)` matters here: one trace of this
# agent is 50-151 runs, so clustering without it would compare middleware chain runs
# against each other instead of comparing conversations.
INSIGHTS_SAMPLE = 100
INSIGHTS_FILTER = "eq(is_root, true)"

# Appended to the summary prompt. Insights templates the prompt per run, and without a
# `{{run.*}}` variable the model is asked to summarize a conversation it was never
# shown. The UI's prompt editor adds exactly this.
INSIGHTS_PROMPT_VARIABLE = "\n\n{{run.inputs}}"


def insights_model_id(client: Any) -> str:
    """A workspace model Insights is allowed to use, or "" if there is none.

    THE reason a job used to fail. A config with `model: "anthropic"` and no
    per-workspace ANTHROPIC_API_KEY answers `422 {"detail": "['ANTHROPIC_API_KEY']"}`,
    which is what every fresh customer workspace looked like. The UI does not ask for a
    key: it points `cluster_model`/`summary_model` at a *playground model setting* — a
    record in `GET /playground-settings` — and the ones backed by LangSmith's own LLM
    gateway (`LC_GATEWAY_KEY`) need no customer credentials at all.

    Requires BOTH `available_in_insights_heavy` (clustering) and
    `available_in_insights_light` (per-run summaries), since one id fills both fields.
    """
    return playground_model_id(
        client, ("available_in_insights_heavy", "available_in_insights_light")
    )


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

    The config mirrors what the UI writes, field for field (captured from a HAR of the
    UI saving one), because the differences were not cosmetic: without
    `cluster_model`/`summary_model` the job needs a per-workspace API key, without
    `filter` it clusters middleware runs instead of conversations, and without
    `{{run.inputs}}` the summariser never sees the conversation.
    """
    session_id = str(client.read_project(project_name=project).id)
    topic = data_gap or "a topic the assistant has no data for"
    name = "Demo: answer quality"
    # Best-effort: a workspace with no insights-capable model still gets a saved
    # config, and the job below reports why it could not run.
    try:
        model_id = insights_model_id(client)
    except Exception:  # noqa: BLE001 - an unreadable model list is the same as none
        model_id = ""
    inner: dict[str, Any] = {
        "name": name,
        "summary_prompt": (
            f"Summarize what the user asked {customer or 'this'} assistant for and how well "
            f"it answered. Call out explicitly when the assistant presented specific figures "
            f"as fact for topics it has no data on — especially '{topic}' — versus when it "
            f"correctly said the data was unavailable." + INSIGHTS_PROMPT_VARIABLE
        ),
        "attribute_schemas": {
            "fabricated_figures": {
                "type": "boolean",
                "filter_by": False,
                "description": (
                    "True if the answer states specific numbers as established fact without "
                    "them being present in the retrieved data."
                ),
            },
            "user_satisfied": {
                "type": "boolean",
                "filter_by": False,
                "description": "True if the user appears satisfied with the answer.",
            },
        },
        "last_n_hours": MAX_BACKDATE_HOURS,
        "sample": INSIGHTS_SAMPLE,
        "filter": INSIGHTS_FILTER,
        "model": "anthropic",
    }
    if model_id:
        # Heavy = clustering, light = per-run summaries. The UI points both at one
        # model, and these take precedence over the `model` above.
        inner["cluster_model"] = model_id
        inner["summary_model"] = model_id
    created = client.request_with_retries(
        "POST", f"/sessions/{session_id}/insights/configs", json={"name": name, "config": inner}
    ).json()
    out: dict[str, Any] = {
        "session_id": session_id,
        "config_id": created.get("id", ""),
        "model": model_id,
    }
    if not (run and out["config_id"]):
        return out
    try:
        # Just the config_id, as the UI sends: the window (`last_n_hours`) is part of
        # the config, and the job inherits it.
        job = client.request_with_retries(
            "POST", f"/sessions/{session_id}/insights", json={"config_id": out["config_id"]}
        ).json()
        out["job_id"] = job.get("id", "")
        out["status"] = job.get("status", "")
    except Exception as exc:  # noqa: BLE001
        # Reachable when the workspace exposes no model Insights may use, in which case
        # the config falls back to `model: "anthropic"` and the API answers a bare 422
        # listing the missing key, e.g. {"detail": "['ANTHROPIC_API_KEY']"}. The config
        # we just saved is still good and the presenter can pick a model and hit Run.
        detail = str(exc)
        out["job_error"] = (
            "insights config saved, but no model in this workspace is available to "
            "Insights — add one (Settings -> Model secrets, or an LLM Gateway model) "
            "and hit Run: " + detail[:200]
            if "API_KEY" in detail
            else f"insights job failed: {detail[:200]}"
        )
    return out


# --- annotation queue -----------------------------------------------------------

# How many traces to queue for human review, and how many of those should be the
# fabricating ones. Ten is a sitting rather than a chore, and 60% gap probes means the
# pattern shows up without the queue becoming one repeated verdict.
QUEUE_SIZE = 10
QUEUE_GAP_SHARE = 0.6

# The two things a reviewer records. `hallucinated` is categorical so the queue renders
# buttons rather than a slider, and `reviewer_notes` is freeform so "which figure was
# invented" can be written down — the detail that turns a score into a bug report.
QUEUE_HALLUCINATION_KEY = "hallucinated"
QUEUE_NOTES_KEY = "reviewer_notes"


def annotation_queue_name(project: str) -> str:
    """Deterministic queue name for a project, so /cleanup can find it without an id.

    Same trick as the judge prompt: the queue is created on the backfill thread, long
    after the assistant's metadata was written, so the name is the handle we can record
    up front and resolve later.
    """
    return f"{project} - hallucination review"


def _ensure_feedback_configs(client: Any) -> None:
    """Define the rubric's feedback keys for the workspace. Idempotent.

    Workspace-level and shared by every queue, which is also why /cleanup leaves them
    alone: deleting a config another demo's queue still points at would break it. An
    existing key answers 409, which is success for our purposes.
    """
    configs: list[tuple[str, dict, bool]] = [
        (
            QUEUE_HALLUCINATION_KEY,
            {
                "type": "categorical",
                "categories": [
                    {"value": 1, "label": "Fabricated — states figures it has no data for"},
                    {"value": 0, "label": "Grounded — or correctly says the data is missing"},
                ],
            },
            True,  # 1 is the bad outcome, so a lower score is better
        ),
        (QUEUE_NOTES_KEY, {"type": "freeform"}, False),
    ]
    for key, config, lower_better in configs:
        try:
            client.create_feedback_config(
                feedback_key=key,
                feedback_config=cast("Any", config),
                is_lower_score_better=lower_better,
            )
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            if not ("409" in msg or "conflict" in msg or "already exists" in msg):
                raise


def ensure_annotation_queue(
    client: Any,
    project: str,
    *,
    reviewable: list[dict],
    customer: str = "",
    data_gap: str = "",
) -> dict:
    """Create the review queue for `project` and fill it with `reviewable` traces.

    The queue is the human half of the demo the Insights cluster shows in aggregate: the
    same fabrications, one trace at a time, with a rubric that says what to look for. It
    is created here rather than at assistant setup because an empty queue is not worth
    opening — the traces have to exist first.

    Reuses an existing queue with our name so a re-seed tops it up instead of stacking
    duplicates.
    """
    out: dict[str, Any] = {"queue": annotation_queue_name(project), "added": 0}
    _ensure_feedback_configs(client)
    existing = next(
        (q for q in client.list_annotation_queues(name=out["queue"], limit=1) or []), None
    )
    if existing is not None:
        out["queue_id"] = str(existing.id)
    else:
        topic = data_gap or "a topic it has no data for"
        who = customer or "this customer"
        queue = client.create_annotation_queue(
            name=out["queue"],
            description=(
                f"Spot-check {who}'s assistant for invented figures, especially around '{topic}'."
            ),
            rubric_instructions=(
                f"You are reviewing answers from {who}'s analytics assistant.\n\n"
                f"The assistant has no data on '{topic}'. A GOOD answer says so. A BAD "
                f"answer presents specific numbers, percentages or trends for it as "
                f"established fact — check the dashboard widgets as well as the prose, "
                f"since invented figures often hide in a KPI card.\n\n"
                f"Mark '{QUEUE_HALLUCINATION_KEY}' for each trace, and use "
                f"'{QUEUE_NOTES_KEY}' to quote the figure that was made up."
            ),
            rubric_items=cast(
                "Any",
                [
                    {
                        "feedback_key": QUEUE_HALLUCINATION_KEY,
                        "description": "Did the answer state figures the data does not support?",
                        "is_required": True,
                    },
                    {
                        "feedback_key": QUEUE_NOTES_KEY,
                        "description": "Which figure was invented? Quote it.",
                        "is_required": False,
                    },
                ],
            ),
        )
        out["queue_id"] = str(queue.id)
    run_ids = [r["run_id"] for r in reviewable if r.get("run_id")]
    if not run_ids:
        return out
    # The runs were ingested seconds ago and the queue add is a read on the server's
    # side, so this is the same visibility race `fetch_trace` handles — retry rather
    # than lose the queue's contents.
    for attempt in range(QUEUE_ADD_ATTEMPTS):
        try:
            client.add_runs_to_annotation_queue(out["queue_id"], run_ids=run_ids)
            out["added"] = len(run_ids)
            return out
        except Exception as exc:  # noqa: BLE001
            if attempt == QUEUE_ADD_ATTEMPTS - 1:
                out["error"] = f"queue created but empty: {str(exc)[:200]}"
                return out
            time.sleep(QUEUE_ADD_DELAY)
    return out


# Same shape of wait as fetch_trace, for the same reason: a just-ingested run is not
# immediately addressable.
QUEUE_ADD_ATTEMPTS = 6
QUEUE_ADD_DELAY = 4.0


# --- engine ---------------------------------------------------------------------

# How often Engine re-scans the project once enabled. This is the UI's own default
# ("0 */6 * * *") and the server rewrites the minute to spread load, so an enabled
# project reads back with a jittered schedule (`51 0/6 * * *`) rather than this
# string — not a mismatch. Every scan spends LCU (see
# `/v1/platform/orgs/current/issues-agent/lcu-spend` for the org's budget), so this
# is the one knob to turn down if demo projects start adding up.
ENGINE_CRON = "0 */6 * * *"


def ensure_engine_job(client: Any, project: str, *, cron: str = ENGINE_CRON) -> dict:
    """Turn Engine (the issues agent) on for `project`, as the UI's toggle does.

    A single `POST /v1/platform/sessions/{id}/issues-agent` with a cron schedule —
    creating the config IS enabling it (`cron_enabled` comes back true) and the first
    scan starts immediately, which is what makes this worth doing at seed time: by the
    time a presenter opens the Engine tab, the agent has already run over the traffic
    we just backfilled instead of showing an empty page and a 6-hour wait.

    Enabling twice is treated as success: the config is per session, so a re-seed of a
    project that already has Engine on answers with a conflict and there is nothing to
    fix.
    """
    session_id = str(client.read_project(project_name=project).id)
    path = f"/v1/platform/sessions/{session_id}/issues-agent"
    out: dict[str, Any] = {"session_id": session_id}
    try:
        created = client.request_with_retries("POST", path, json={"cron_schedule": cron}).json()
    except Exception as exc:  # noqa: BLE001 - Engine is a garnish; never fail the traffic
        detail = str(exc)
        if "409" in detail or "conflict" in detail.lower() or "already" in detail.lower():
            out["already_enabled"] = True
            return out
        out["error"] = f"engine could not be enabled: {detail[:200]}"
        return out
    out["config_id"] = created.get("id", "")
    # Read back rather than assume: `cron_enabled` is the field the UI's toggle
    # reflects, and the schedule is the server's jittered version of `cron`.
    out["enabled"] = bool(created.get("cron_enabled"))
    out["cron_schedule"] = created.get("cron_schedule", "")
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
    with_engine: bool = True,
    with_queue: bool = True,
) -> dict:
    """Seed real runs, backfill a day of traffic, then start Insights and Engine on it.

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
            seeds = run_seeds(
                context, seed_questions(actions, data_gap), project=project, client=client
            )
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
    # Insights and Engine both key off the traffic above, and both are extras: the
    # payload is the traffic, so each failure is recorded and the other still runs.
    if with_insights:
        try:
            result["insights"] = ensure_insights_job(
                client, project, customer=customer, data_gap=data_gap
            )
        except Exception as exc:  # noqa: BLE001 - the traffic is the payload; insights is extra
            result["insights_error"] = f"{type(exc).__name__}: {exc}"
    if with_engine:
        try:
            result["engine"] = ensure_engine_job(client, project)
        except Exception as exc:  # noqa: BLE001 - ditto: a demo without Engine still demos
            result["engine_error"] = f"{type(exc).__name__}: {exc}"
    if with_queue:
        try:
            result["queue"] = ensure_annotation_queue(
                client,
                project,
                reviewable=result.get("reviewable") or [],
                customer=customer,
                data_gap=data_gap,
            )
        except Exception as exc:  # noqa: BLE001 - ditto
            result["queue_error"] = f"{type(exc).__name__}: {exc}"
    return result


# --- one backfill per project --------------------------------------------------
#
# Both entry points go through `start_demo_traffic`: the daemon thread
# `prepare_assistant` spawns at setup, and `POST /demo-traffic` (which exists so an
# assistant created before the automatic backfill can still be given traffic). The
# bookkeeping lives HERE rather than in webapp.py so the two share it — while the
# route owned it, the setup run was invisible to the route, which meant the panel
# showed the pre-backfill empty state through a running backfill and Generate would
# cheerfully start a second one on top of it. Two at once on one project doubles the
# traffic and burns the hourly ingest quota, which is the thing this guards.
#
# Hints, not truth: the traffic itself is durable and visible in the project, and a
# redeploy losing the receipt costs nothing (see `GET /demo-traffic/status`).
_LOCK = threading.Lock()
_INFLIGHT: dict[str, float] = {}
_RESULT: dict[str, dict] = {}

# A backfill is minutes of work on a DAEMON thread, so a process restart mid-run
# leaves a slot claimed by a thread that no longer exists. Past this the slot is
# treated as dead and the next caller may have it — otherwise one killed backfill
# disables Generate for that project until the next redeploy.
_STALE_SECS = 1800


def start_demo_traffic(workspace: str, project: str, **kwargs: Any) -> dict:
    """Spawn a backfill for `project` on a daemon thread. Never raises.

    Returns a receipt — `{ok, project, running}`, or `{ok, already_running}` when one
    is already live for this project. `kwargs` are forwarded to
    `generate_demo_traffic`; progress is read back with `demo_traffic_state`.
    """
    with _LOCK:
        started = _INFLIGHT.get(project, 0.0)
        if started and (time.time() - started) < _STALE_SECS:
            return {"ok": True, "project": project, "already_running": True}
        _INFLIGHT[project] = time.time()

    def _body() -> None:
        try:
            _RESULT[project] = generate_demo_traffic(workspace, project, **kwargs)
        except Exception as exc:  # noqa: BLE001 - unreachable by contract, kept anyway
            # `generate_demo_traffic` reports failures in its return value, so getting
            # here means it broke its own contract. Print the traceback: this thread is
            # fire-and-forget and the stored string is all anyone would otherwise see.
            traceback.print_exc()
            _RESULT[project] = {"error": f"{type(exc).__name__}: {exc}"}
        finally:
            with _LOCK:
                _INFLIGHT.pop(project, None)

    try:
        threading.Thread(target=_body, daemon=True).start()
    except Exception as exc:  # noqa: BLE001 - a thread we could not start is the caller's news
        with _LOCK:
            _INFLIGHT.pop(project, None)
        return {"ok": False, "project": project, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "project": project, "running": True}


def demo_traffic_state(project: str) -> dict:
    """`{running, result}` for `project` — the two things LangSmith cannot tell us."""
    with _LOCK:
        started = _INFLIGHT.get(project, 0.0)
        running = bool(started) and (time.time() - started) < _STALE_SECS
    return {"running": running, "result": _RESULT.get(project) or {}}
