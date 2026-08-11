"""Unit tests for the synthetic demo-traffic backfill (dashboard_agent/demo_traffic.py).

No network and no API keys — CI runs pytest with ANTHROPIC_API_KEY/LANGSMITH_API_KEY
deliberately unset. Everything here exercises the pure remap/scheduling logic against
a hand-built stand-in for a real trace.

The load-bearing property is that `shift_trace` produces a tree LangSmith will accept
and render: root `id == trace_id`, every descendant carrying the root's trace_id, and
`dotted_order` that both parses and encodes the ancestor chain. A malformed
dotted_order does not error — it silently renders as a flat or broken trace, which is
exactly the kind of thing nobody notices until it is on a projector.
"""

from __future__ import annotations

import datetime as dt
import random
import threading
import time
import types
import uuid

import pytest

from dashboard_agent import demo_traffic as DT

_DO_TS = "%Y%m%dT%H%M%S%fZ"


def _run(name, run_type, start, dur_s, parent=None, order=None, usage=None, meta=None):
    """A stand-in for a langsmith Run, with only the attributes shift_trace reads."""
    rid = uuid.uuid4()
    seg = start.strftime(_DO_TS) + str(rid)
    extra = {"metadata": dict(meta or {})}
    if usage:
        extra["metadata"]["usage_metadata"] = usage
    return types.SimpleNamespace(
        id=rid,
        parent_run_id=parent,
        dotted_order=f"{order}.{seg}" if order else seg,
        name=name,
        run_type=run_type,
        start_time=start,
        end_time=start + dt.timedelta(seconds=dur_s),
        inputs={"messages": [{"role": "user", "content": "hi"}]},
        outputs={"output": "there"},
        extra=extra,
        tags=["seq:step:1"],
        serialized={"id": ["ChatAnthropic"]},
    )


@pytest.fixture
def trace():
    """A 4-run trace: root -> (model -> llm), tool. Mirrors the real nesting shape."""
    t0 = dt.datetime(2026, 8, 1, 12, 0, 0, tzinfo=dt.UTC)
    root = _run("dashboard_agent", "chain", t0, 20, meta={"failure_mode": "hallucination"})
    model = _run("model", "chain", t0 + dt.timedelta(seconds=1), 8, root.id, root.dotted_order)
    llm = _run(
        "ChatAnthropic",
        "llm",
        t0 + dt.timedelta(seconds=2),
        6,
        model.id,
        model.dotted_order,
        usage={
            "input_tokens": 1000,
            "output_tokens": 100,
            "total_tokens": 1100,
            "input_token_details": {"cache_read": 900},
        },
        meta={"ls_provider": "anthropic", "ls_model_name": "claude-sonnet-5"},
    )
    tool = _run("datasearch", "tool", t0 + dt.timedelta(seconds=11), 3, root.id, root.dotted_order)
    return [root, model, llm, tool]


# --- shift_trace: the tree must stay a tree ------------------------------------


def test_shift_trace_preserves_tree_structure(trace):
    when = dt.datetime(2026, 8, 4, 9, 30, tzinfo=dt.UTC)
    out = DT.shift_trace(trace, when, project="P", rng=random.Random(0))

    assert len(out) == len(trace)
    root = out[0]
    assert root["id"] == root["trace_id"], "root id must equal trace_id"
    assert "parent_run_id" not in root
    by_id = {r["id"]: r for r in out}
    for run in out:
        assert run["trace_id"] == root["trace_id"], "all runs share the root's trace_id"
        assert run["session_name"] == "P"
        if "parent_run_id" in run:
            parent = by_id[run["parent_run_id"]]
            assert run["dotted_order"].startswith(parent["dotted_order"] + ".")


def test_dotted_order_parses_and_matches_start_time(trace):
    when = dt.datetime(2026, 8, 4, 9, 30, tzinfo=dt.UTC)
    for run in DT.shift_trace(trace, when, project="P", rng=random.Random(0)):
        seg = run["dotted_order"].split(".")[-1]
        assert seg[-36:] == run["id"], "segment tail is the run id"
        # The 6-digit microsecond field is mandatory; strptime is how LangSmith reads it.
        parsed = dt.datetime.strptime(seg[:-36], _DO_TS).replace(tzinfo=dt.UTC)
        assert parsed == dt.datetime.fromisoformat(run["start_time"])


def test_shift_moves_root_to_requested_time_and_keeps_children_enclosed(trace):
    when = dt.datetime(2026, 8, 4, 9, 30, tzinfo=dt.UTC)
    out = DT.shift_trace(trace, when, project="P", rng=random.Random(0), duration_scale=2.0)
    root = out[0]
    assert dt.datetime.fromisoformat(root["start_time"]) == when
    # A whole-trace scale must not let a child escape its parent's window.
    for run in out:
        assert root["start_time"] <= run["start_time"] <= root["end_time"]
        assert run["end_time"] <= root["end_time"]


def test_duration_scale_stretches_the_whole_trace(trace):
    when = dt.datetime(2026, 8, 4, 9, 30, tzinfo=dt.UTC)

    def span(scale):
        out = DT.shift_trace(trace, when, project="P", rng=random.Random(0), duration_scale=scale)
        r = out[0]
        return (
            dt.datetime.fromisoformat(r["end_time"]) - dt.datetime.fromisoformat(r["start_time"])
        ).total_seconds()

    assert span(2.0) == pytest.approx(span(1.0) * 2)


# --- metadata, tokens, marking -------------------------------------------------


def test_every_run_is_marked_synthetic(trace):
    out = DT.shift_trace(trace, dt.datetime.now(dt.UTC), project="P", rng=random.Random(0))
    for run in out:
        assert run["extra"]["metadata"]["synthetic"] is True
        assert DT.SYNTHETIC_TAG in run["tags"]


def test_thread_and_user_are_set_on_every_run_not_just_the_root(trace):
    # Thread rollups require the thread id on children too, not only the root.
    out = DT.shift_trace(
        trace,
        dt.datetime.now(dt.UTC),
        project="P",
        rng=random.Random(0),
        thread_id="T",
        user_id="U",
    )
    assert {r["extra"]["metadata"]["thread_id"] for r in out} == {"T"}
    assert {r["extra"]["metadata"]["user_id"] for r in out} == {"U"}


def test_token_jitter_scales_and_drops_disallowed_keys():
    # validate_extracted_usage_metadata rejects unknown keys and takes the whole
    # ingest batch down with them, so the filter is not cosmetic.
    out = DT._jitter_usage(
        {
            "input_tokens": 1000,
            "output_tokens": 100,
            "total_tokens": 1100,
            "input_token_details": {"cache_read": 900},
            "bogus_key": 5,
            "prompt_tokens": 1000,
        },
        0.5,
    )
    assert set(out) <= {
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "input_token_details",
        "output_token_details",
    }
    assert out["input_tokens"] == 500
    assert out["total_tokens"] == out["input_tokens"] + out["output_tokens"]


def test_cache_read_never_exceeds_scaled_input_tokens():
    out = DT._jitter_usage(
        {"input_tokens": 1000, "output_tokens": 10, "input_token_details": {"cache_read": 1000}},
        0.5,
    )
    assert out["input_token_details"]["cache_read"] <= out["input_tokens"]


# --- errors --------------------------------------------------------------------


def test_error_propagates_from_a_leaf_up_to_the_root(trace):
    out = DT.shift_trace(
        trace, dt.datetime.now(dt.UTC), project="P", rng=random.Random(3), error="boom"
    )
    errored = [r for r in out if r.get("error")]
    assert errored, "an error must be applied somewhere"
    assert out[0].get("error") == "boom", "the root must show the failure"
    # Every errored run is an ancestor-or-self chain, so their dotted_orders nest.
    orders = sorted((r["dotted_order"] for r in errored), key=len)
    for shorter, longer in zip(orders, orders[1:], strict=False):
        assert longer.startswith(shorter)


# --- scheduling ----------------------------------------------------------------


def test_schedule_stays_inside_the_window_and_never_in_the_future():
    now = dt.datetime(2026, 8, 4, 12, 0, tzinfo=dt.UTC)
    stamps = DT._schedule(23, 200, random.Random(0), now=now)
    assert stamps == sorted(stamps)
    assert all(now - dt.timedelta(hours=23) <= s < now for s in stamps)


def test_schedule_is_capped_at_the_server_backdate_limit():
    # Anything older than MAX_BACKDATE_HOURS is dropped by the ingest API, so asking
    # for a week must not silently generate six days of runs that never land.
    now = dt.datetime(2026, 8, 4, 12, 0, tzinfo=dt.UTC)
    stamps = DT._schedule(24 * 7, 300, random.Random(0), now=now)
    oldest = now - dt.timedelta(hours=DT.MAX_BACKDATE_HOURS)
    assert all(s >= oldest for s in stamps)


# --- seed questions ------------------------------------------------------------


def test_seed_questions_reads_the_gap_tag():
    actions = [
        {"question": "grounded one"},
        {"question": "grounded two"},
        {"question": "the gap probe", "kind": "gap"},
    ]
    out = DT.seed_questions(actions, "widgets per quarter")
    assert [q["is_gap"] for q in out] == [False, False, True]


def test_seed_questions_falls_back_to_the_last_action_for_untagged_legacy_assistants():
    # Assistants provisioned before the gap tag existed still have the probe last.
    actions = [{"question": "a"}, {"question": "b"}, {"question": "c"}]
    out = DT.seed_questions(actions, "some gap")
    assert [q["is_gap"] for q in out] == [False, False, True]


def test_seed_questions_tags_nothing_when_there_is_no_gap():
    out = DT.seed_questions([{"question": "a"}, {"question": "b"}], "")
    assert not any(q["is_gap"] for q in out)


def test_seed_questions_skips_actions_without_a_question():
    assert DT.seed_questions([{"label": "no question"}, {"question": "q"}], "") == [
        {"question": "q", "is_gap": False}
    ]


# --- backfill ------------------------------------------------------------------


class _FakeClient:
    """Captures ingested runs instead of talking to LangSmith."""

    def __init__(self, traces: list | None = None):
        self.runs: list[dict] = []
        self.feedback: list[dict] = []
        self.flushed = 0
        # Successive answers for list_runs, so a test can make a trace show up late.
        self._traces = list(traces or [])
        self.reads = 0

    def multipart_ingest(self, create=None, **_):
        self.runs.extend(create or [])

    def create_feedback(self, **kw):
        self.feedback.append(kw)

    def flush(self):
        self.flushed += 1

    def list_runs(self, **_):
        # Holds on the last answer once the script runs out, so a caller that reads
        # until the trace stops growing sees it stay put rather than vanish.
        self.reads += 1
        if not self._traces:
            return []
        return self._traces.pop(0) if len(self._traces) > 1 else list(self._traces[0])


# --- seeding --------------------------------------------------------------------
#
# The seed runs and the read-back have to agree on a WORKSPACE. A LangSmith key picks
# one, so tracing without an explicit client sends the seeds to the ambient key's
# workspace while the backfill reads the customer's — the seeds are then invisible and
# the whole backfill fails as "no seed traces" after paying for the runs.


def _collected(start_offset_s: float):
    """A collect_runs entry: locally rootlike, as every one of them is."""
    rid = uuid.uuid4()
    return types.SimpleNamespace(
        id=rid,
        trace_id=rid,
        parent_run_id=None,
        start_time=dt.datetime(2026, 8, 1, 12, tzinfo=dt.UTC)
        + dt.timedelta(seconds=start_offset_s),
    )


def test_collected_trace_id_picks_the_outermost_run():
    # Ordered by COMPLETION: the inner model call finishes first, so the real root
    # (started first, ended last) is at the END. Taking [0] gave a child id that
    # matches no trace server-side, which is what emptied every backfill.
    root = _collected(0)
    inner = [_collected(1.2), _collected(3.5)]
    assert DT.collected_trace_id([*inner, root]) == str(root.id)


def test_collected_trace_id_handles_one_run_and_none():
    root = _collected(0)
    assert DT.collected_trace_id([root]) == str(root.id)
    assert DT.collected_trace_id([]) == ""


def test_collected_trace_id_survives_a_run_without_a_start_time():
    root, broken = _collected(0), types.SimpleNamespace(id=uuid.uuid4(), start_time=None)
    assert DT.collected_trace_id([broken, root]) == str(root.id)


def test_run_seeds_traces_with_the_workspace_client(monkeypatch):
    from langsmith import get_tracing_context

    seen: dict = {}

    class _Agent:
        def invoke(self, payload, config=None, context=None):
            # Read the live context rather than a mock of tracing_context: what matters
            # is the client langchain_core would hand the tracer, which is this one.
            seen.update(get_tracing_context())
            return {}

    monkeypatch.setattr("dashboard_agent.agent.build_agent", lambda: _Agent())
    monkeypatch.setattr("dashboard_agent.assistant_evals.make_run_context", lambda c: c)
    client = _FakeClient()

    DT.run_seeds({}, [{"question": "q", "is_gap": False}], project="P", client=client)

    assert seen["client"] is client
    assert seen["project_name"] == "P"
    # Flushed before the caller reads the traces back — the tracer uploads async.
    assert client.flushed == 1


def test_run_seeds_keeps_going_when_one_question_raises(monkeypatch):
    class _Agent:
        def __init__(self):
            self.calls = 0

        def invoke(self, payload, config=None, context=None):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("model is down")
            return {}

    agent = _Agent()
    monkeypatch.setattr("dashboard_agent.agent.build_agent", lambda: agent)
    monkeypatch.setattr("dashboard_agent.assistant_evals.make_run_context", lambda c: c)

    DT.run_seeds({}, [{"question": "a"}, {"question": "b"}], project="P", client=_FakeClient())

    assert agent.calls == 2


def test_fetch_trace_waits_for_a_trace_that_is_not_indexed_yet(monkeypatch, trace):
    # A seed is read seconds after it finished, so the first reads legitimately come
    # back empty; giving up on the first one loses the trace we just paid to produce.
    monkeypatch.setattr(DT.time, "sleep", lambda _s: None)
    client = _FakeClient(traces=[[], [], trace])

    assert DT.fetch_trace(client, "P", "t") == sorted(trace, key=lambda r: r.dotted_order)
    # Two empty reads, the trace, then the confirming read that it stopped growing.
    assert client.reads == 4


def test_fetch_trace_waits_for_a_half_indexed_trace_to_settle(trace, monkeypatch):
    # The runs of one trace are indexed in batches, so a read can catch a root with
    # only some of its children — cloning THAT would replay as a truncated trace.
    monkeypatch.setattr(DT.time, "sleep", lambda _s: None)
    client = _FakeClient(traces=[trace[:1], trace[:2], trace])

    assert len(DT.fetch_trace(client, "P", "t")) == len(trace)
    assert client.reads == 4


def test_fetch_trace_gives_up_after_the_last_attempt(monkeypatch):
    monkeypatch.setattr(DT.time, "sleep", lambda _s: None)
    client = _FakeClient()

    assert DT.fetch_trace(client, "P", "t", attempts=3) == []
    assert client.reads == 3


def test_backfill_emits_marked_traces_and_reports_a_summary(trace):
    client = _FakeClient()
    seeds = [
        {"trace_id": "a", "is_gap": True, "runs": trace},
        {"trace_id": "b", "is_gap": False, "runs": trace},
    ]
    summary = DT.backfill(client, "P", seeds, count=50, rng=random.Random(5))

    assert summary["traces"] == 50
    assert summary["runs"] == len(client.runs)
    assert all(r["extra"]["metadata"]["synthetic"] for r in client.runs)
    # Every emitted trace is flagged one way or the other, so a presenter can filter.
    roots = [r for r in client.runs if "parent_run_id" not in r]
    assert len(roots) == 50
    assert all("demo_gap_probe" in r["extra"]["metadata"] for r in roots)


def test_backfill_gap_share_is_respected_on_average():
    # A single seed is a small sample, so average across seeds rather than asserting
    # on one draw — the per-run share legitimately varies by ±10 points at this size.
    t0 = dt.datetime(2026, 8, 1, 12, tzinfo=dt.UTC)
    runs = [_run("dashboard_agent", "chain", t0, 5)]
    shares = []
    for s in range(25):
        client = _FakeClient()
        summary = DT.backfill(
            client,
            "P",
            [{"trace_id": "g", "is_gap": True, "runs": runs}, {"trace_id": "n", "runs": runs}],
            count=100,
            rng=random.Random(s),
        )
        shares.append(summary["gap_traces"] / summary["traces"])
    assert DT.GAP_SHARE - 0.05 < sum(shares) / len(shares) < DT.GAP_SHARE + 0.05


def test_backfill_without_seeds_is_a_clean_no_op():
    client = _FakeClient()
    summary = DT.backfill(client, "P", [], count=10, rng=random.Random(0))
    assert summary["traces"] == 0
    assert client.runs == []
    assert "error" in summary


def test_backfill_survives_feedback_failures(trace):
    class Hostile(_FakeClient):
        def create_feedback(self, **kw):
            raise RuntimeError("feedback is down")

    client = Hostile()
    # Feedback is a garnish; losing it must not lose the traffic.
    summary = DT.backfill(
        client, "P", [{"trace_id": "a", "runs": trace}], count=20, rng=random.Random(1)
    )
    assert summary["traces"] == 20
    assert client.runs


def test_generate_demo_traffic_reports_failures_instead_of_raising(monkeypatch):
    # Called from a daemon thread during assistant setup — it must never raise.
    monkeypatch.setattr(DT, "_ws_client", lambda ws: (_ for _ in ()).throw(RuntimeError("nope")))
    out = DT.generate_demo_traffic("ws", "P", context={}, actions=[])
    assert "error" in out and "nope" in out["error"]


# --- one backfill per project --------------------------------------------------
#
# The registry is what makes the setup-path backfill and POST /demo-traffic interlock.
# Both entry points share it, so a presenter clicking Generate while setup's backfill
# is still running must be refused rather than doubling the traffic.


@pytest.fixture
def registry():
    """A clean registry, restored after the test (module-level, process-wide state)."""
    DT._INFLIGHT.clear()
    DT._RESULT.clear()
    yield DT
    DT._INFLIGHT.clear()
    DT._RESULT.clear()


def _await_idle(project, timeout=5.0):
    """Wait for the backfill thread to leave _INFLIGHT (its `finally`)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if project not in DT._INFLIGHT:
            return True
        time.sleep(0.01)
    return False


def test_start_demo_traffic_records_the_receipt(registry, monkeypatch):
    monkeypatch.setattr(
        DT, "generate_demo_traffic", lambda ws, p, **kw: {"project": p, "traces": 7}
    )
    ack = DT.start_demo_traffic("ws", "P", customer="Acme")
    assert ack["ok"] and ack["running"]
    assert _await_idle("P")
    # The receipt is what the panel reads back through GET /demo-traffic/status.
    assert DT.demo_traffic_state("P") == {"running": False, "result": {"project": "P", "traces": 7}}


def test_start_demo_traffic_refuses_a_second_run_for_the_same_project(registry, monkeypatch):
    release = threading.Event()
    calls = []

    def _slow(ws, p, **kw):
        calls.append(p)
        release.wait(5)
        return {"traces": 1}

    monkeypatch.setattr(DT, "generate_demo_traffic", _slow)
    first = DT.start_demo_traffic("ws", "P")
    second = DT.start_demo_traffic("ws", "P")
    other = DT.start_demo_traffic("ws", "Q")
    try:
        assert first["running"] is True
        # The case this guards: double the traffic and the hourly ingest quota.
        assert second == {"ok": True, "project": "P", "already_running": True}
        assert DT.demo_traffic_state("P")["running"] is True
        # A different project is unrelated — the guard is per project, not global.
        assert other["running"] is True
    finally:
        release.set()
    assert _await_idle("P") and _await_idle("Q")
    # Two threads ran, not three: the refused call never reached generate_demo_traffic.
    assert sorted(calls) == ["P", "Q"]


def test_a_stale_slot_does_not_disable_generate_forever(registry, monkeypatch):
    monkeypatch.setattr(DT, "generate_demo_traffic", lambda ws, p, **kw: {"traces": 1})
    # A process restart mid-backfill leaves a slot claimed by a thread that is gone.
    DT._INFLIGHT["P"] = time.time() - DT._STALE_SECS - 1
    assert DT.demo_traffic_state("P")["running"] is False
    assert DT.start_demo_traffic("ws", "P")["running"] is True
    assert _await_idle("P")


def test_start_demo_traffic_never_raises_when_the_thread_will_not_start(registry, monkeypatch):
    def _no_threads(*a, **kw):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(DT.threading, "Thread", _no_threads)
    ack = DT.start_demo_traffic("ws", "P")
    assert ack["ok"] is False and "can't start new thread" in ack["error"]
    # The slot must be released, or the failure locks the project out until redeploy.
    assert "P" not in DT._INFLIGHT
