"""Spec for the backend wiring: sandbox default (execute) + `/skills/` → Context Hub.

Uses a FAKE raw sandbox wrapped in the REAL `LangSmithSandbox`, a monkeypatched
`SandboxClient`, and a monkeypatched `ContextHubBackend` — no live VM, no network,
CI-safe.

deepagents 0.7 removed the callable backend factory, so per-run selection now lives
in `_resolve_backends(runtime) -> (default, routes)` (the logic) behind a single
concrete `DynamicBackend(CompositeBackend)` whose `.default`/`.routes` resolve per run
via `get_runtime()`. These tests exercise `_resolve_backends` directly (deterministic,
no graph) and assert the execute gate the way deepagents does — `supports_execution`
keys off a composite's `.default`.

The spec:
- Assistant with skills + sandbox available → sandbox default (⇒ `execute` offered) +
  `/skills/` route to Context Hub. World: skills readable/writable in CH, code
  runnable in the VM.
- Sandbox unavailable / `DA_SANDBOX=0` → StateBackend default (no `execute`); skills
  still mount if present.
- Back-compat: an old Context Hub assistant (`agent_repo`, no `skills_repo`) keeps the
  whole-repo ContextHubBackend (today's behavior, no execute).
- Caching: `_resolve_backends` runs per model/tool call, so N calls create ONE VM,
  seed ONCE.
- `DynamicBackend` off a run (build/import/tests) falls back to plain state, so graph
  load never fails.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from deepagents.backends import CompositeBackend, LangSmithSandbox, StateBackend
from deepagents.backends.protocol import FileDownloadResponse, FileInfo, LsResult
from deepagents.middleware.filesystem import supports_execution

from dashboard_agent import agent as A


class _FakeRun:
    def __init__(self, stdout="", stderr="", exit_code=0):
        self.stdout, self.stderr, self.exit_code = stdout, stderr, exit_code


class _FakeSandbox:
    """Stand-in for a langsmith `Sandbox` — records the shell commands it runs."""

    def __init__(self, name: str):
        self.name = name
        self.runs: list[str] = []

    def run(self, command: str, timeout: int | None = None) -> _FakeRun:
        self.runs.append(command)
        return _FakeRun(stdout="ok")

    def write(self, path: str, content: bytes) -> None:  # SDK write path (unused here)
        pass


class _FakeClient:
    """Stand-in for `langsmith.sandbox.SandboxClient`."""

    def __init__(self, **_):
        self.created: list[str] = []
        self.existing: list[_FakeSandbox] = []

    def list_sandboxes(self, **_):
        return list(self.existing)

    def create_sandbox(self, *, name=None, **_):
        self.created.append(name or "unnamed")
        sb = _FakeSandbox(name or "unnamed")
        self.existing.append(sb)
        return sb


@pytest.fixture(autouse=True)
def _clear_caches():
    A._SANDBOX_CACHE.clear()
    A._CTXHUB_CACHE.clear()
    yield
    A._SANDBOX_CACHE.clear()
    A._CTXHUB_CACHE.clear()


def _rt(**ctx):
    return SimpleNamespace(context=ctx)


def _install_client(monkeypatch, client=None):
    client = client or _FakeClient()
    monkeypatch.setenv("DA_SANDBOX", "1")
    # `_get_or_create_sandbox` short-circuits to None when no LangSmith key is set
    # (CI has none — this avoids a real network attempt). Tests that exercise the
    # sandbox path must supply a placeholder so the FAKE client below is reached.
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")
    monkeypatch.setattr(A, "SandboxClient", lambda **kw: client)
    return client


def _stub_ctxhub(monkeypatch):
    """ContextHubBackend that never hits the network; carries the repo it mounted."""
    monkeypatch.setattr(
        A, "ContextHubBackend", lambda repo, client=None: SimpleNamespace(_repo=repo)
    )


def _execute_offered(default, routes) -> bool:
    """Mirror how deepagents gates `execute` per run: off a CompositeBackend's default."""
    return supports_execution(CompositeBackend(default=default, routes=routes))


# --- skills + sandbox compose: sandbox default (execute) + /skills/ route ---


def test_skills_and_sandbox_compose(monkeypatch):
    _install_client(monkeypatch)
    _stub_ctxhub(monkeypatch)
    default, routes = A._resolve_backends(_rt(customer="Eval Co", skills_repo="eval-skills"))
    assert isinstance(default, LangSmithSandbox)  # ⇒ execute offered
    assert _execute_offered(default, routes) is True
    assert "/skills/" in routes  # skills mounted live from Context Hub


def test_skills_mount_without_sandbox_has_no_execute(monkeypatch):
    monkeypatch.setenv("DA_SANDBOX", "0")  # sandbox off
    _stub_ctxhub(monkeypatch)
    default, routes = A._resolve_backends(_rt(customer="Eval Co", skills_repo="eval-skills"))
    assert isinstance(default, StateBackend)
    assert _execute_offered(default, routes) is False  # no VM ⇒ no execute
    assert "/skills/" in routes  # ...but skills still mount


# --- sandbox alone (no skills) → sandbox default, no routes, execute available ---


def test_sandbox_only_is_plain_backend(monkeypatch):
    _install_client(monkeypatch)
    default, routes = A._resolve_backends(_rt(customer="Eval Co"))  # no skills/agent repo
    assert isinstance(default, LangSmithSandbox)
    assert routes == {}
    assert _execute_offered(default, routes) is True


def test_available_seeds_data_stack_and_dataset_once(monkeypatch):
    client = _install_client(monkeypatch)
    A._resolve_backends(_rt(customer="Eval Co"))
    cmds = [c for sb in client.existing for c in sb.runs]
    assert len(cmds) == 1  # one seed call on create
    seed = cmds[0]
    assert "sales.csv" in seed  # dataset written to the VM
    # the data-analysis stack is pre-installed so the first forecast turn is instant
    assert "pip install" in seed
    assert "pandas" in seed and "numpy" in seed and "statsmodels" in seed and "scikit-learn" in seed


# --- unavailable / disabled → StateBackend, no execute ---


def test_client_failure_falls_back_to_statebackend(monkeypatch):
    monkeypatch.setenv("DA_SANDBOX", "1")
    monkeypatch.setenv(
        "LANGSMITH_API_KEY", "test-key"
    )  # reach the client (its raise), not the no-key guard

    def _boom(**_):
        raise RuntimeError("sandbox service not enabled")

    monkeypatch.setattr(A, "SandboxClient", _boom)
    default, routes = A._resolve_backends(_rt(customer="Eval Co"))
    assert isinstance(default, StateBackend)
    assert _execute_offered(default, routes) is False


def test_no_langsmith_key_skips_sandbox(monkeypatch):
    # No LangSmith credentials (e.g. CI) → skip the sandbox cleanly, no network
    # attempt — even with DA_SANDBOX on and a client installed.
    monkeypatch.setenv("DA_SANDBOX", "1")
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LS_CROSS_WORKSPACE_KEY", raising=False)
    client = _FakeClient()
    monkeypatch.setattr(A, "SandboxClient", lambda **kw: client)
    default, _routes = A._resolve_backends(_rt(customer="Eval Co"))
    assert isinstance(default, StateBackend)
    assert client.created == []


def test_env_kill_switch_disables_sandbox(monkeypatch):
    client = _FakeClient()
    monkeypatch.setenv("DA_SANDBOX", "0")
    monkeypatch.setattr(A, "SandboxClient", lambda **kw: client)
    default, _routes = A._resolve_backends(_rt(customer="Eval Co"))
    assert isinstance(default, StateBackend)
    assert client.created == []  # never even constructed a VM


# --- DynamicBackend: off a run, degrade to plain state so graph load never fails ---


def test_dynamic_backend_offrun_falls_back_to_state():
    # No active graph run → get_runtime() raises → safe StateBackend default, no routes,
    # execute NOT offered. This is what makes create_deep_agent(backend=DynamicBackend())
    # importable/compilable at build time.
    backend = A.DynamicBackend()
    assert isinstance(backend.default, StateBackend)
    assert backend.routes == {}
    assert supports_execution(backend) is False


# --- pre-warm at provisioning: create+seed up front so the first chat is warm ---


def test_prewarm_creates_and_seeds_then_runtime_reuses(monkeypatch):
    client = _install_client(monkeypatch)
    A.prewarm_sandbox(customer="Eval Co")
    assert len(client.created) == 1  # VM created at provisioning time
    # The agent runtime is a different process → its cache is empty; it must still
    # reattach the pre-warmed VM by name rather than create/seed a second one.
    A._SANDBOX_CACHE.clear()
    default, _routes = A._resolve_backends(_rt(customer="Eval Co"))
    assert isinstance(default, LangSmithSandbox)  # warm VM, execute available
    assert len(client.created) == 1  # reattached by name, not recreated
    seeds = [c for sb in client.existing for c in sb.runs if "sales.csv" in c]
    assert len(seeds) == 1  # seeded once (at pre-warm), not again on the first turn


def test_prewarm_uses_agent_repo_precedence(monkeypatch):
    # Runtime keys on agent_repo (else customer); pre-warm must match so it warms
    # the SAME named VM the runtime will later reattach.
    client = _install_client(monkeypatch)
    A.prewarm_sandbox(agent_repo="acme-agent", customer="Acme")
    assert client.created == ["da-acme-agent"]  # agent_repo wins over customer


def test_prewarm_noop_when_disabled(monkeypatch):
    client = _FakeClient()
    monkeypatch.setenv("DA_SANDBOX", "0")
    monkeypatch.setattr(A, "SandboxClient", lambda **kw: client)
    A.prewarm_sandbox(customer="Eval Co")
    assert client.created == []  # kill switch respected — no VM at provisioning


# --- back-compat: old Context Hub assistant (agent_repo, no skills_repo) ---


def test_legacy_context_hub_assistant_keeps_whole_repo(monkeypatch):
    client = _install_client(monkeypatch)
    _stub_ctxhub(monkeypatch)
    default, routes = A._resolve_backends(_rt(agent_repo="acme-agent", ls_workspace="ws"))
    assert getattr(default, "_repo", None) == "acme-agent"  # ContextHubBackend, whole FS
    assert routes == {}
    assert client.created == []  # no sandbox for the legacy path


# --- caching: resolver runs per call → create ONCE, seed ONCE, reuse the VM object ---


def test_sandbox_created_and_seeded_once_across_calls(monkeypatch):
    client = _install_client(monkeypatch)
    rt = _rt(customer="Eval Co")
    defaults = [A._resolve_backends(rt)[0] for _ in range(5)]
    assert len(client.created) == 1  # one VM for five resolver calls
    assert {id(b) for b in defaults} == {id(defaults[0])}  # same cached VM object
    seed_runs = [c for sb in client.existing for c in sb.runs if "sales.csv" in c]
    assert len(seed_runs) == 1


def test_existing_vm_reused_not_recreated(monkeypatch):
    client = _FakeClient()
    client.existing.append(_FakeSandbox("da-eval-co"))  # a VM survived a restart
    _install_client(monkeypatch, client)
    A._resolve_backends(_rt(customer="Eval Co"))
    assert client.created == []  # reused by name, not recreated


# --- routing contract: skills at the mounted repo's ROOT surface through the route ---


class _RootSkills:
    """A ContextHub-like backend whose ROOT holds skill dirs (the bundle layout)."""

    files = {"dashboard/SKILL.md": "# dash", "returns/SKILL.md": "# ret"}

    def ls(self, path="/"):
        pre = path.strip("/")
        dirs: set[str] = set()
        ents: list[FileInfo] = []
        for k in self.files:
            if pre and not k.startswith(pre + "/"):
                continue
            rel = k[len(pre) + 1 :] if pre else k
            top = rel.split("/", 1)[0]
            if "/" in rel:
                if top not in dirs:
                    dirs.add(top)
                    ents.append(FileInfo(path=f"/{pre + '/' if pre else ''}{top}", is_dir=True))
            else:
                ents.append(FileInfo(path=f"/{k}", is_dir=False))
        return LsResult(entries=ents)

    def download_files(self, paths):
        out = []
        for p in paths:
            c = self.files.get(p.lstrip("/"))
            out.append(
                FileDownloadResponse(
                    path=p, content=c.encode() if c else None, error=None if c else "not found"
                )
            )
        return out


def test_root_layout_bundle_surfaces_through_route():
    comp = CompositeBackend(
        default=LangSmithSandbox(cast("Any", _FakeSandbox("fake"))),
        routes=cast("dict[str, Any]", {"/skills/": _RootSkills()}),
    )
    dirs = {e["path"] for e in (comp.ls("/skills/").entries or [])}
    assert dirs == {"/skills/dashboard", "/skills/returns"}  # discovery sees the skills
    dl = comp.download_files(["/skills/dashboard/SKILL.md"])
    assert dl[0].content == b"# dash" and dl[0].error is None  # read maps to the repo


# --- prompt: the model is told about the VM + seeded data (Level 0, no model) ---


def test_sandbox_note_points_at_seeded_data(monkeypatch):
    monkeypatch.setenv("DA_SANDBOX", "1")
    note = A._sandbox_note(_rt())
    assert "execute" in note and "/workspace/data" in note and "push_widget" in note


def test_sandbox_note_empty_when_disabled(monkeypatch):
    monkeypatch.setenv("DA_SANDBOX", "0")
    assert A._sandbox_note(_rt()) == ""
