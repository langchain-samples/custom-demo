"""Spec for the eval routes: `POST /evals/run`, `GET /evals/status`, `/cleanup` cascade.

Fast, offline, no LLM, no LangSmith, no API keys — a fake client stands in for
LangSmith and a fake runner stands in for the experiment, following the pattern in
`test_sandbox_files_routes.py`.

The contract these routes have to hold up:
- `/evals/run` **returns immediately**. Three real agent runs take 30-90s; the demo
  cannot have the presenter's click block on that, so the work goes to a daemon
  thread (the `prewarm_sandbox` fire-and-forget pattern) and the response is a
  receipt, not a result. A run that blows up on that thread must not reach the SPA
  as a 500 or take the server with it.
- `/evals/status` derives the SCORE entirely from LangSmith on every call, so the
  panel survives a page reload, a second browser, or a redeploy mid-demo. `running`
  is inferred from an experiment having fewer scored rows than the dataset has
  examples, with a staleness cutoff so a dead run stops spinning.
- The only server-side state is what LangSmith cannot know: a run still on a thread
  here, and why the last one died. A run that dies while BUILDING the target (no
  model key, an unreachable workspace) never creates an experiment, so without
  `_LAST_RUN_ERROR` the panel would show the previous score untouched and the
  presenter would read a dead run as "my prompt fix did nothing".
- Both degrade CALMLY when there is no dataset: assistants created before this
  feature have none, and the panel must render an empty state, never an error.
- `/cleanup` deletes the eval dataset alongside the other artifacts, independently —
  one failing deletion must not strand the rest.
- The LangSmith key stays server-side; nothing in any response body leaks it.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from langsmith.utils import LangSmithNotFoundError
from starlette.testclient import TestClient

import dashboard_agent.assistant_evals as AE
import dashboard_agent.webapp as W

client = TestClient(W.app)

WORKSPACE = "ws-acme"
DATASET = "acme-freight-demo-evals"
DATASET_URL = f"https://smith.langchain.com/datasets/{DATASET}"
EXPERIMENT_URL = "https://smith.langchain.com/projects/exp-1"


# --- fakes ------------------------------------------------------------------------


class _FakeDataset:
    def __init__(self, name: str = DATASET, example_count: int = 3):
        self.id = "ds-1"
        self.name = name
        self.url = DATASET_URL
        self.example_count = example_count


class _FakeExperiment:
    """A LangSmith tracing project produced by `evaluate()` over the dataset."""

    def __init__(
        self, *, passed: int = 2, scored: int = 3, age_secs: float = 60.0, url=EXPERIMENT_URL
    ):
        self.id = "exp-1"
        self.name = f"{DATASET}-baseline"
        self.url = url
        self.start_time = datetime.now(UTC) - timedelta(seconds=age_secs)
        self.reference_dataset_id = "ds-1"
        # LangSmith's shape. `_score_from_feedback` recovers the pass count as
        # round(avg * n), so avg has to be the real ratio, not a rounded one.
        self.feedback_stats = {
            AE.EVAL_FEEDBACK_KEY: {"n": scored, "avg": (passed / scored) if scored else 0.0}
        }


class _FakeClient:
    """Minimal LangSmith stand-in: dataset lookup, experiment listing, deletion."""

    def __init__(self, *, dataset: _FakeDataset | None = None, experiments=(), boom: str = ""):
        self._dataset = dataset
        self._experiments = list(experiments)
        self._boom = boom
        self.deleted: list[Any] = []
        self.listed: list[dict] = []

    # Both the existence check and the read are implemented, and both honour `boom`,
    # so the outage/empty-state tests hold whichever one the route reaches for.
    def has_dataset(self, *, dataset_name: str) -> bool:
        if self._boom == "read":
            raise RuntimeError("503 from LangSmith")
        return self._dataset is not None and dataset_name == self._dataset.name

    def read_dataset(self, *, dataset_name: str, **_) -> _FakeDataset:
        if self._boom == "read":
            raise RuntimeError("503 from LangSmith")
        if self._dataset is None:
            raise LangSmithNotFoundError(f"Dataset {dataset_name} not found")
        return self._dataset

    def list_projects(self, **kwargs):
        self.listed.append(kwargs)
        return iter(self._experiments)

    def delete_dataset(self, **kwargs):
        if self._boom == "dataset":
            raise RuntimeError("no permission")
        self.deleted.append(("dataset", kwargs.get("dataset_name") or kwargs.get("dataset_id")))

    def delete_project(self, *, project_name: str):
        self.deleted.append(("project", project_name))

    def delete_prompt(self, name: str):
        self.deleted.append(("prompt", name))

    def delete_agent(self, name: str):
        self.deleted.append(("agent", name))

    def delete_skill(self, name: str):
        self.deleted.append(("skill", name))


@pytest.fixture(autouse=True)
def _clean_run_state():
    """The in-flight/last-error maps are module-level; a leak crosses tests.

    Cleared before AND after, so a test that spawns a run cannot make an unrelated
    one report `running` or an error it never provoked.
    """
    W._INFLIGHT.clear()
    W._LAST_RUN_ERROR.clear()
    yield
    W._INFLIGHT.clear()
    W._LAST_RUN_ERROR.clear()


def _await_idle(timeout: float = 5.0) -> bool:
    """Wait for the background run to leave `_INFLIGHT` (its thread's `finally`)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not W._INFLIGHT:
            return True
        time.sleep(0.02)
    return False


def _install_client(monkeypatch, fake: _FakeClient) -> _FakeClient:
    monkeypatch.setattr(W, "_scoped_client", lambda *_a, **_k: fake)
    return fake


def _install_runner(monkeypatch, fn) -> None:
    """Replace the experiment runner the background thread imports."""
    monkeypatch.setattr(AE, "run_experiment", fn)


def _raise(exc_name: str, message: str):
    """A runner that dies the way a real one does — before LangSmith sees anything."""

    def _runner(*_a, **_k):
        raise {"ValueError": ValueError, "RuntimeError": RuntimeError}[exc_name](message)

    return _runner


def _status(**params) -> dict:
    resp = client.get("/evals/status", params={"workspace": WORKSPACE, **params})
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- POST /evals/run: a receipt, not a result ---------------------------------------


def test_run_returns_before_the_experiment_finishes(monkeypatch):
    """The click must not block for the 30-90s of three real agent runs."""
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def _slow_runner(*_a, **_k):
        started.set()
        release.wait(timeout=5)  # bounded so a regression fails instead of hanging
        finished.set()

    _install_runner(monkeypatch, _slow_runner)
    try:
        resp = client.post("/evals/run", json={"workspace": WORKSPACE, "dataset": DATASET})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "dataset": DATASET, "running": True}
        assert started.wait(timeout=5), "the experiment was never spawned"
        assert not finished.is_set(), "the route waited for the experiment, it did not spawn it"
    finally:
        release.set()
        finished.wait(timeout=5)


def test_run_hands_the_runner_the_assistant_context(monkeypatch):
    """The experiment must grade the SAME agent the demo runs.

    Prompt handle, planted gap, tool selection: if the assistant's stored context
    does not reach `run_experiment`, the experiment scores a different assistant.
    """
    seen: list[tuple] = []
    done = threading.Event()
    context = {"prompt_name": "acme-system", "data_gap": "csat", "customer": "Acme Freight"}

    def _runner(*args, **kwargs):
        seen.append((args, kwargs))
        done.set()

    _install_runner(monkeypatch, _runner)
    client.post(
        "/evals/run",
        json={
            "workspace": WORKSPACE,
            "dataset": DATASET,
            "context": context,
            "experiment_prefix": "acme-baseline",
        },
    )
    assert done.wait(timeout=5)
    args, kwargs = seen[0]
    assert args[:3] == (WORKSPACE, DATASET, context)
    assert kwargs["experiment_prefix"] == "acme-baseline"


def test_run_accepts_the_dataset_name_alias(monkeypatch):
    """The SPA posts back the object /evals/status handed it, keyed `dataset_name`."""
    done = threading.Event()
    _install_runner(monkeypatch, lambda *a, **k: done.set())
    resp = client.post("/evals/run", json={"workspace": WORKSPACE, "dataset_name": DATASET})
    assert resp.status_code == 200
    assert done.wait(timeout=5)


def test_run_rejects_a_request_with_no_dataset(monkeypatch):
    called: list = []
    _install_runner(monkeypatch, lambda *a, **k: called.append(1))
    resp = client.post("/evals/run", json={"workspace": WORKSPACE})
    assert resp.status_code == 400
    assert called == []


def test_run_rejects_invalid_json(monkeypatch):
    _install_runner(monkeypatch, lambda *a, **k: None)
    resp = client.post("/evals/run", content=b"not json")
    assert resp.status_code == 400


def test_run_never_leaks_the_key(monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-secret-key")
    monkeypatch.setenv("LS_CROSS_WORKSPACE_KEY", "lsv2-secret-key")
    done = threading.Event()
    _install_runner(monkeypatch, lambda *a, **k: done.set())
    resp = client.post("/evals/run", json={"workspace": WORKSPACE, "dataset": DATASET})
    assert "lsv2-secret-key" not in resp.text
    done.wait(timeout=5)


def test_a_failing_experiment_never_reaches_the_presenter_as_a_crash(monkeypatch):
    """A run that blows up on the detached thread must not 500 or kill the server."""

    def _boom(*_a, **_k):
        raise RuntimeError("langsmith exploded")

    _install_runner(monkeypatch, _boom)
    _install_client(
        monkeypatch, _FakeClient(dataset=_FakeDataset(), experiments=[_FakeExperiment()])
    )
    assert client.post("/evals/run", json={"dataset": DATASET}).status_code == 200
    assert _await_idle()
    body = _status(dataset=DATASET)
    # The score still comes from LangSmith, so a dead run leaves the previous number
    # standing — calmer mid-demo than an error dialog, and the presenter can retry.
    assert body["passed"] == 2
    assert body["running"] is False  # and the panel stops spinning


def test_a_dead_run_tells_the_panel_why(monkeypatch):
    """The failure the score alone cannot show.

    The target is built (build_agent, the Anthropic key, the scoped client) BEFORE
    `client.evaluate` creates anything, so the likeliest failures leave no experiment
    and no trace — LangSmith keeps reporting the previous, complete run. Silently,
    that is a presenter who fixed the prompt, clicked Run, and watched 2/3 not move.
    """
    _install_runner(monkeypatch, _raise("ValueError", "ANTHROPIC_API_KEY is not set"))
    _install_client(
        monkeypatch, _FakeClient(dataset=_FakeDataset(), experiments=[_FakeExperiment()])
    )
    client.post("/evals/run", json={"dataset": DATASET})
    assert _await_idle()
    body = _status(dataset=DATASET)
    assert "ANTHROPIC_API_KEY is not set" in body["last_error"]
    # About the RUN, not about this lookup: the status call itself succeeded.
    assert not body.get("error")
    assert body["passed"] == 2


def test_a_new_run_clears_the_previous_failure(monkeypatch):
    """Retrying must not leave the old error under a run that is working."""
    _install_runner(monkeypatch, _raise("RuntimeError", "transient"))
    _install_client(monkeypatch, _FakeClient(dataset=_FakeDataset(), experiments=[]))
    client.post("/evals/run", json={"dataset": DATASET})
    assert _await_idle()
    assert _status(dataset=DATASET)["last_error"]

    _install_runner(monkeypatch, lambda *a, **k: None)
    client.post("/evals/run", json={"dataset": DATASET})
    assert _await_idle()
    assert not _status(dataset=DATASET).get("last_error")


def test_status_reports_a_run_langsmith_cannot_see_yet(monkeypatch):
    """The first seconds after the click, before the experiment project exists.

    Without this the panel would show the OLD score with an enabled Run button while
    a run is already burning three agent turns — and invite a second click.
    """
    release = threading.Event()
    _install_runner(monkeypatch, lambda *a, **k: release.wait(timeout=5))
    _install_client(monkeypatch, _FakeClient(dataset=_FakeDataset(), experiments=[]))
    try:
        client.post("/evals/run", json={"dataset": DATASET})
        assert _status(dataset=DATASET)["running"] is True
    finally:
        release.set()
    assert _await_idle()


def test_a_second_click_does_not_start_a_second_experiment(monkeypatch):
    """Three real agent runs per click; a double-click must not buy two of them.

    Two concurrent experiments over one dataset also race for which one the badge
    ends up showing.
    """
    release = threading.Event()
    runs: list[int] = []

    def _slow(*_a, **_k):
        runs.append(1)
        release.wait(timeout=5)

    _install_runner(monkeypatch, _slow)
    try:
        first = client.post("/evals/run", json={"dataset": DATASET}).json()
        second = client.post("/evals/run", json={"dataset": DATASET}).json()
        assert first == {"ok": True, "dataset": DATASET, "running": True}
        # Not an error: the caller wanted a run in flight, and there is one.
        assert second["ok"] is True and second["already_running"] is True
        assert len(runs) == 1
    finally:
        release.set()
    assert _await_idle()


def test_a_finished_run_frees_the_dataset_for_another(monkeypatch):
    """The dedupe is per in-flight run, not a one-shot lock on the dataset."""
    _install_runner(monkeypatch, lambda *a, **k: None)

    def _start() -> dict:
        return client.post("/evals/run", json={"dataset": DATASET}).json()

    assert _start().get("already_running") is None
    assert _await_idle()
    assert _start().get("already_running") is None
    assert _await_idle()


# --- GET /evals/status: LangSmith is the source of truth -------------------------------


def test_status_reports_the_latest_experiment_score(monkeypatch):
    """2/3 is the baseline the demo opens on — the gap example is the one that fails."""
    fake = _install_client(
        monkeypatch,
        _FakeClient(dataset=_FakeDataset(), experiments=[_FakeExperiment(passed=2, scored=3)]),
    )
    body = _status(dataset=DATASET)
    assert body["dataset_name"] == DATASET
    assert body["dataset_url"] == DATASET_URL
    assert body["exists"] is True
    assert (body["passed"], body["total"]) == (2, 3)
    assert body["experiment_name"] == f"{DATASET}-baseline"
    # A link out to THIS experiment. Whether that is the project page or the
    # dataset's compare view is a UX call; identifying the experiment is not.
    assert body["url"].startswith("https://smith.langchain.com/") and "exp-1" in body["url"]
    assert body["running"] is False
    # Scoped to THIS dataset's experiments, with the stats that carry the scores.
    assert fake.listed[0]["reference_dataset_id"] == "ds-1"
    assert fake.listed[0]["include_stats"] is True


def test_status_reports_a_green_run_after_the_fix(monkeypatch):
    """The other half of the demo: the same dataset reads 3/3 once the prompt is fixed."""
    _install_client(
        monkeypatch,
        _FakeClient(dataset=_FakeDataset(), experiments=[_FakeExperiment(passed=3, scored=3)]),
    )
    body = _status(dataset=DATASET)
    assert (body["passed"], body["total"]) == (3, 3)


def test_status_picks_the_newest_experiment(monkeypatch):
    """`list_projects` promises no ordering, and the re-run is the one on screen."""
    stale = _FakeExperiment(passed=2, scored=3, age_secs=3600)
    stale.id, stale.name = "exp-old", "baseline"
    fresh = _FakeExperiment(passed=3, scored=3, age_secs=30)
    for order in ([fresh, stale], [stale, fresh]):
        _install_client(monkeypatch, _FakeClient(dataset=_FakeDataset(), experiments=order))
        assert _status(dataset=DATASET)["passed"] == 3


def test_status_still_links_out_when_the_sdk_gives_no_project_url(monkeypatch):
    """The panel's "open in LangSmith" link is the payoff; it must never be blank."""
    _install_client(
        monkeypatch,
        _FakeClient(dataset=_FakeDataset(), experiments=[_FakeExperiment(url=None)]),
    )
    assert _status(dataset=DATASET)["url"] == f"{DATASET_URL}/compare?selectedSessions=exp-1"


def test_status_reports_a_run_still_in_flight(monkeypatch):
    """Fewer scored rows than examples, recently started → the panel keeps polling."""
    _install_client(
        monkeypatch,
        _FakeClient(
            dataset=_FakeDataset(example_count=3),
            experiments=[_FakeExperiment(passed=1, scored=1, age_secs=5)],
        ),
    )
    body = _status(dataset=DATASET)
    assert body["running"] is True
    assert body["scored"] == 1
    assert body["total"] == 3  # the badge denominator is the dataset, not the progress


def test_status_gives_up_on_a_stale_run(monkeypatch):
    """A run that died partway must not spin the panel forever."""
    _install_client(
        monkeypatch,
        _FakeClient(
            dataset=_FakeDataset(example_count=3),
            experiments=[_FakeExperiment(passed=1, scored=1, age_secs=W._RUN_STALE_SECS + 60)],
        ),
    )
    assert _status(dataset=DATASET)["running"] is False


def test_status_is_calm_when_the_assistant_has_no_dataset(monkeypatch):
    """Assistants created before this feature have none. Empty state, not an error."""
    _install_client(monkeypatch, _FakeClient(dataset=None))
    body = _status(dataset=DATASET)
    assert body["exists"] is False
    assert body["running"] is False
    assert not body.get("error")


def test_status_with_no_dataset_param_is_an_empty_state(monkeypatch):
    """The SPA calls this before it knows whether the assistant has a dataset."""
    called: list = []
    monkeypatch.setattr(W, "_scoped_client", lambda *a, **k: called.append(1))
    body = _status()
    assert body["exists"] is False
    assert called == []  # no dataset, no LangSmith round trip


def test_status_reports_a_dataset_with_no_experiment_yet(monkeypatch):
    """Between provisioning and the baseline finishing there is a dataset, no score."""
    _install_client(monkeypatch, _FakeClient(dataset=_FakeDataset(), experiments=[]))
    body = _status(dataset=DATASET)
    assert body["dataset_name"] == DATASET
    assert body["exists"] is True
    assert body["experiment_name"] is None
    assert body["running"] is False
    assert body.get("total") is None
    assert not body.get("error")


def test_status_never_leaks_the_key(monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-secret-key")
    monkeypatch.setenv("LS_CROSS_WORKSPACE_KEY", "lsv2-secret-key")
    _install_client(
        monkeypatch, _FakeClient(dataset=_FakeDataset(), experiments=[_FakeExperiment()])
    )
    resp = client.get("/evals/status", params={"workspace": WORKSPACE, "dataset": DATASET})
    assert "lsv2-secret-key" not in resp.text


def test_status_survives_a_langsmith_outage(monkeypatch):
    """An outage is a JSON error the panel can render, never a rendered stack trace."""
    _install_client(monkeypatch, _FakeClient(boom="read"))
    resp = client.get("/evals/status", params={"workspace": WORKSPACE, "dataset": DATASET})
    assert resp.status_code == 500
    assert resp.json()["error"] == "RuntimeError: 503 from LangSmith"
    assert "Traceback" not in resp.text


# --- POST /cleanup: the dataset joins the cascade ----------------------------------------


def test_cleanup_deletes_the_eval_dataset(monkeypatch):
    fake = _install_client(monkeypatch, _FakeClient())
    body = client.post("/cleanup", json={"workspace": WORKSPACE, "eval_dataset": DATASET}).json()
    assert ("dataset", DATASET) in fake.deleted
    assert any(DATASET in entry for entry in body["deleted"])
    assert body["failed"] == []


def test_cleanup_without_a_dataset_deletes_nothing_extra(monkeypatch):
    """Pre-feature assistants carry no `eval_dataset`; the cascade must not invent one."""
    fake = _install_client(monkeypatch, _FakeClient())
    client.post("/cleanup", json={"workspace": WORKSPACE, "project": "Acme-corebot-demo"})
    assert [kind for kind, _ in fake.deleted] == ["project"]


def test_cleanup_dataset_failure_is_isolated(monkeypatch):
    """Best-effort and INDEPENDENT: a dataset we cannot delete must not strand the rest."""
    fake = _install_client(monkeypatch, _FakeClient(boom="dataset"))
    body = client.post(
        "/cleanup",
        json={
            "workspace": WORKSPACE,
            "eval_dataset": DATASET,
            "project": "Acme-corebot-demo",
            "prompt_name": "acme-system",
        },
    ).json()
    assert ("project", "Acme-corebot-demo") in fake.deleted
    assert ("prompt", "acme-system") in fake.deleted
    assert any(DATASET in f["artifact"] for f in body["failed"])
    assert any("Acme-corebot-demo" in entry for entry in body["deleted"])
