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

import json
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

    def __init__(self, name: str, status: str = "ready"):
        self.name = name
        self.status = status
        self.runs: list[str] = []

    def run(self, command: str, timeout: int | None = None) -> _FakeRun:
        self.runs.append(command)
        return _FakeRun(stdout="ok")

    def write(self, path: str, content: bytes) -> None:  # SDK write path (unused here)
        pass


class _FakeClient:
    """Stand-in for `langsmith.sandbox.SandboxClient`, with the TTL lifecycle."""

    def __init__(self, **_):
        self.created: list[str] = []
        self.existing: list[_FakeSandbox] = []
        self.started: list[str] = []
        self.retention: list[tuple[int | None, int | None]] = []

    def list_sandboxes(self, **_):
        return list(self.existing)

    def create_sandbox(self, *, name=None, idle_ttl_seconds=None, delete_after_stop_seconds=None):
        self.created.append(name or "unnamed")
        self.retention.append((idle_ttl_seconds, delete_after_stop_seconds))
        sb = _FakeSandbox(name or "unnamed")
        self.existing.append(sb)
        return sb

    def get_sandbox_status(self, name: str):
        sb = next((s for s in self.existing if s.name == name), None)
        if sb is None:
            raise RuntimeError(f"404 sandbox {name} not found")  # a deleted VM
        return SimpleNamespace(status=sb.status)

    def start_sandbox(self, name: str, **_):
        sb = next((s for s in self.existing if s.name == name), None)
        if sb is None:
            raise RuntimeError(f"404 sandbox {name} not found")
        self.started.append(name)
        sb.status = "ready"
        return sb

    def _reap(self) -> None:
        """What the server's sweep does to an idle VM: stop it, then delete it."""
        self.existing.clear()


@pytest.fixture(autouse=True)
def _clear_caches():
    A._SANDBOX_CACHE.clear()
    A._SANDBOX_SEEN.clear()
    A._CTXHUB_CACHE.clear()
    yield
    A._SANDBOX_CACHE.clear()
    A._SANDBOX_SEEN.clear()
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


# --- coming back later: the VM stops, gets deleted, and the cache outlives both ---
#
# A cached handle is not evidence the VM exists. It stops after an hour idle and is
# deleted a week after that, while this process (and its cache) keeps running — which
# is how a demo picked up the next day ended up failing every command with
# SandboxConnectionError instead of getting a VM back.


def _expire_cache(key: str = "Eval Co") -> None:
    """Age the cache entry past `_SANDBOX_REVALIDATE_AFTER` without sleeping."""
    A._SANDBOX_SEEN[key] = A._SANDBOX_SEEN[key] - A._SANDBOX_REVALIDATE_AFTER - 1


def test_fresh_cache_entry_is_trusted_without_asking_the_service(monkeypatch):
    client = _install_client(monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(client, "get_sandbox_status", lambda name: calls.append(name))
    first, _ = A._resolve_backends(_rt(customer="Eval Co"))
    again, _ = A._resolve_backends(_rt(customer="Eval Co"))
    # An active conversation resolves backends on every model and tool call; none of
    # those may turn into a status round trip.
    assert again is first
    assert calls == []


def test_stale_cache_entry_is_revalidated_and_kept_when_the_vm_lives(monkeypatch):
    client = _install_client(monkeypatch)
    first, _ = A._resolve_backends(_rt(customer="Eval Co"))
    _expire_cache()
    again, _ = A._resolve_backends(_rt(customer="Eval Co"))
    assert again is first  # same warm VM, no churn
    assert client.created == ["da-eval-co"]  # and nothing new provisioned


def test_a_stopped_vm_is_restarted_rather_than_replaced(monkeypatch):
    client = _install_client(monkeypatch)
    A._resolve_backends(_rt(customer="Eval Co"))
    client.existing[0].status = "stopped"  # an hour idle
    _expire_cache()

    default, _ = A._resolve_backends(_rt(customer="Eval Co"))

    assert isinstance(default, LangSmithSandbox)
    assert client.started == ["da-eval-co"]  # nothing auto-starts it; we must
    assert client.created == ["da-eval-co"]  # same VM, so its files survive
    assert len([c for sb in client.existing for c in sb.runs]) == 1  # not reseeded


def test_a_deleted_vm_is_replaced_and_reseeded(monkeypatch):
    client = _install_client(monkeypatch)
    A._resolve_backends(_rt(customer="Eval Co"))
    client._reap()  # stopped long enough to be swept
    _expire_cache()

    default, routes = A._resolve_backends(_rt(customer="Eval Co"))

    assert isinstance(default, LangSmithSandbox)  # a working VM, not the dead handle
    assert _execute_offered(default, routes) is True
    assert client.created == ["da-eval-co", "da-eval-co"]
    # The dataset died with the VM, so the replacement has to be seeded again.
    assert [c for sb in client.existing for c in sb.runs] != []


def test_an_unavailable_vm_degrades_instead_of_serving_a_dead_handle(monkeypatch):
    client = _install_client(monkeypatch)
    A._resolve_backends(_rt(customer="Eval Co"))
    client._reap()
    _expire_cache()
    monkeypatch.setattr(
        client, "create_sandbox", lambda **_: (_ for _ in ()).throw(RuntimeError("no capacity"))
    )

    default, routes = A._resolve_backends(_rt(customer="Eval Co"))

    assert isinstance(default, StateBackend)  # no execute beats execute-that-throws
    assert _execute_offered(default, routes) is False
    assert "Eval Co" not in A._SANDBOX_CACHE


def test_attach_only_callers_never_provision_a_replacement(monkeypatch):
    # The /sandbox-files browser is a UI click; it may not trigger a ~30s boot.
    client = _install_client(monkeypatch)
    A._resolve_backends(_rt(customer="Eval Co"))
    client._reap()
    _expire_cache()

    assert A._ensure_sandbox("Eval Co", create=False) is None
    assert client.created == ["da-eval-co"]


def test_a_stopped_vm_is_kept_for_a_week(monkeypatch):
    client = _install_client(monkeypatch)
    A._resolve_backends(_rt(customer="Eval Co"))
    idle_ttl, delete_after_stop = client.retention[0]
    # Stopping costs nothing to keep, so retention is long enough that tomorrow's
    # demo restarts this VM with its data instead of rebuilding it.
    assert idle_ttl == 3600
    assert delete_after_stop == 7 * 24 * 3600


# --- prompt: the model is told about the VM + seeded data (Level 0, no model) ---


def test_sandbox_note_points_at_seeded_data(monkeypatch):
    monkeypatch.setenv("DA_SANDBOX", "1")
    note = A._sandbox_note(_rt())
    assert "execute" in note and "/workspace/data" in note and "push_widget" in note


def test_sandbox_note_never_promises_a_particular_dataset(monkeypatch):
    """Reported by a user: a medical-PDF use case was told to load a sales CSV.

    The seed is sales-shaped for every assistant, so the prompt must send the model to
    LOOK rather than assert what is there.
    """
    monkeypatch.setenv("DA_SANDBOX", "1")
    note = A._sandbox_note(_rt())
    assert "ls /workspace/data" in note
    assert "never assume a particular file exists" in note
    assert "sales" not in note.lower()


def test_sandbox_note_routes_file_requests_to_the_upload_button(monkeypatch):
    """Also reported: the agent asked for a PDF it had no way to receive.

    There is exactly one channel now, and the prompt has to name it and rule out the
    plausible-sounding alternatives that strand the conversation.
    """
    monkeypatch.setenv("DA_SANDBOX", "1")
    note = A._sandbox_note(_rt())
    assert "Files panel" in note
    assert "paste" in note and "attach it to the chat" in note
    # And it can actually read what arrives, without a 30s install mid-demo.
    assert "pypdf" in note and "pypdf" in A._SEED_SCRIPT


def test_sandbox_note_empty_when_disabled(monkeypatch):
    monkeypatch.setenv("DA_SANDBOX", "0")
    assert A._sandbox_note(_rt()) == ""


# --- per-use-case seed: the VM gets THIS assistant's files ------------------------
#
# Reported by a user: a medical-PDF use case was handed 24 months of retail revenue,
# because the seed was one hardcoded script for every assistant. The spec is now
# model-authored data rendered by fixed code — never model-authored code.

_MEDICAL_SEED = [
    {
        "name": "intake_2026-01.pdf",
        "kind": "pdf",
        "description": "Scanned patient intake form",
        "text": "Patient 4172\nAdmitted 2026-01-04\nDiagnosis: pneumonia",
    },
    {
        "name": "claims.csv",
        "kind": "csv",
        "description": "Claim lines by month",
        "columns": ["claim_id", "amount_usd", "status"],
        "rows": [["C-1", "1200", "denied"], ["C-2", "840", "paid"]],
    },
]


def test_seed_script_writes_the_use_cases_own_files():
    script = A.render_seed_script(_MEDICAL_SEED)
    assert "intake_2026-01.pdf" in script and "claims.csv" in script
    assert "sales.csv" not in script  # the retail default is gone, not merely reordered
    # PDFs need a library the base image lacks.
    assert "fpdf2" in script
    # The data reaches the VM as JSON inside a QUOTED heredoc, so the shell expands
    # nothing the model wrote.
    assert "<<'SPEC'" in script
    assert json.loads(script.split("<<'SPEC'\n", 1)[1].split("\nSPEC\n", 1)[0])["files"]


def test_seed_script_installs_no_pdf_library_when_nothing_needs_one():
    script = A.render_seed_script(
        [{"name": "a.csv", "kind": "csv", "columns": ["x"], "rows": [["1"]]}]
    )
    assert "fpdf2" not in script


def test_seed_spec_is_treated_as_untrusted_input():
    script = A.render_seed_script(
        [
            {"name": "../../etc/passwd", "kind": "csv", "columns": ["a"], "rows": [["1"]]},
            {"name": ".env", "kind": "txt", "text": "KEY=secret"},
            {"name": "ok.txt", "kind": "exe", "text": "no"},  # unknown kind
            {"name": "fine.txt", "kind": "txt", "text": "yes"},
        ]
    )
    spec = json.loads(script.split("<<'SPEC'\n", 1)[1].split("\nSPEC\n", 1)[0])
    names = [f["name"] for f in spec["files"]]
    # Basename only, no dotenv, no unknown kinds — same rules as an upload.
    assert names == ["passwd", "fine.txt"]


def test_seed_spec_is_capped():
    many = [
        {"name": f"f{i}.csv", "kind": "csv", "columns": ["x"], "rows": [["1"]] * 99}
        for i in range(9)
    ]
    spec = json.loads(A.render_seed_script(many).split("<<'SPEC'\n", 1)[1].split("\nSPEC\n", 1)[0])
    assert len(spec["files"]) == A._SEED_MAX_FILES
    assert all(len(f["rows"]) <= A._SEED_MAX_ROWS for f in spec["files"])


def test_an_assistant_with_no_spec_still_gets_something_to_analyse():
    # Assistants created before this feature, and any setup run whose spec was unusable.
    assert A.render_seed_script([]) == ""
    assert A.render_seed_script([{"name": "", "kind": "csv"}]) == ""


def test_seeding_prefers_the_assistants_spec_over_the_default(monkeypatch):
    ran: list[str] = []
    backend = SimpleNamespace(execute=lambda script: ran.append(script))
    A._seed_data(cast("Any", backend), _MEDICAL_SEED)
    assert "claims.csv" in ran[0] and "sales.csv" not in ran[0]

    ran.clear()
    A._seed_data(cast("Any", backend), None)
    assert "sales.csv" in ran[0]  # the fallback, unchanged


def test_a_lazily_created_vm_seeds_from_the_runs_context(monkeypatch):
    # The prewarm is fire-and-forget: if it lost the race, or the VM was reaped, the
    # first turn must plant the same files rather than the retail default.
    client = _install_client(monkeypatch)
    A._resolve_backends(_rt(customer="Mercy Health", sandbox_seed=_MEDICAL_SEED))
    seeded = [c for sb in client.existing for c in sb.runs]
    assert len(seeded) == 1
    assert "intake_2026-01.pdf" in seeded[0]


def test_sandbox_note_names_the_seeded_files(monkeypatch):
    monkeypatch.setenv("DA_SANDBOX", "1")
    note = A._sandbox_note(_rt(sandbox_seed=_MEDICAL_SEED))
    assert "/workspace/data/intake_2026-01.pdf: Scanned patient intake form" in note
    assert "/workspace/data/claims.csv" in note


def test_pdf_lines_step_down_the_page():
    """Regression: the first live run of this wrote .txt for every PDF.

    `multi_cell(w=0)` leaves the cursor at the RIGHT margin by default, so the second
    line has zero width and fpdf raises "Not enough horizontal space to render a single
    character" — which the fallback then swallowed into a silent .pdf -> .txt downgrade.
    """
    script = A.render_seed_script(_MEDICAL_SEED)
    assert 'new_x="LMARGIN"' in script and 'new_y="NEXT"' in script
    # And the downgrade is no longer silent.
    assert "pdf unavailable, wrote text instead" in script
