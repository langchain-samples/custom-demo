"""Spec for the sandbox file-browser routes: `GET /sandbox-files` + `GET /sandbox-file`.

Fast, offline, no LLM, no VM — a fake backend (and, for two wire-fidelity tests, a
fake raw sandbox wrapped in the REAL `LangSmithSandbox`).

The contract:
- `/sandbox-files` lists ONE directory, dirs first then case-insensitive name, each
  entry tagged `kind` ∈ dir|text|binary so the UI can grey out what it can't render.
  Capped at 500 entries with an honest `truncated` flag.
- `/sandbox-file` reads one file. Non-allowlisted extensions are classified in OUR
  code and never reach the VM — that gate is what keeps a core dump off deepagents'
  unbounded base64 branch. Binary/oversized/non-UTF-8 are 200 placeholders, not errors.
- Paths are confined to the root by normpath + prefix (so `..` can't escape).
- Sandbox off / never provisioned → a calm 503 with a `reason` slug, never a 500 stack.
- The browse path is attach-only: `_ensure_sandbox(key, create=False)` must never
  provision a VM, and the key mirrors the runtime (agent_repo → customer → default).
"""

from __future__ import annotations

import pytest
from deepagents.backends import LangSmithSandbox
from deepagents.backends.protocol import FileData, FileInfo, LsResult, ReadResult
from starlette.testclient import TestClient

import dashboard_agent.agent as A
import dashboard_agent.webapp as W

client = TestClient(W.app)


@pytest.fixture(autouse=True)
def _clear_sandbox_cache():
    A._SANDBOX_CACHE.clear()
    yield
    A._SANDBOX_CACHE.clear()


class _FakeBackend:
    """Minimal sandbox backend: records calls, returns REAL LsResult/ReadResult."""

    def __init__(self, ls: LsResult | None = None, read: ReadResult | None = None):
        self._ls = ls or LsResult(entries=[])
        self._read = read or ReadResult(file_data=FileData(content="", encoding="utf-8"))
        self.calls: list[tuple] = []

    @property
    def id(self) -> str:
        return "da-test"

    async def als(self, path: str) -> LsResult:
        self.calls.append(("als", path))
        return self._ls

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        self.calls.append(("aread", file_path, offset, limit))
        return self._read


def _install(monkeypatch, backend, enabled: bool = True):
    """Patch out the sandbox plumbing (works because webapp imports it call-time)."""
    monkeypatch.setattr(A, "_sandbox_enabled", lambda: enabled)
    monkeypatch.setattr(A, "_ensure_sandbox", lambda key, *, create=True: backend)
    return backend


def _entries(*specs) -> LsResult:
    return LsResult(entries=[FileInfo(path=p, is_dir=d) for p, d in specs])


# --- list: shape, ordering, kinds ------------------------------------------------


def test_list_orders_dirs_first_then_case_insensitive(monkeypatch):
    _install(
        monkeypatch,
        _FakeBackend(
            ls=_entries(
                ("/workspace/data/Zeta.csv", False),
                ("/workspace/data/apple.md", False),
                ("/workspace/data/chart.png", False),
                ("/workspace/data/out", True),
                ("/workspace/data/Archive", True),
            )
        ),
    )
    body = client.get("/sandbox-files?path=/workspace/data").json()
    assert [e["name"] for e in body["entries"]] == [
        "Archive",
        "out",
        "apple.md",
        "chart.png",
        "Zeta.csv",
    ]
    by_name = {e["name"]: e for e in body["entries"]}
    assert by_name["out"] == {
        "name": "out",
        "path": "/workspace/data/out",
        "is_dir": True,
        "kind": "dir",
    }
    assert by_name["apple.md"]["kind"] == "text"
    assert by_name["chart.png"]["kind"] == "binary"  # greyed out before any click
    assert body["root"] == "/workspace"
    assert body["path"] == "/workspace/data"
    assert body["parent"] == "/workspace"
    assert body["sandbox_id"] == "da-test"
    assert body["truncated"] is False


def test_list_defaults_to_root_and_root_has_no_parent(monkeypatch):
    fake = _install(monkeypatch, _FakeBackend(ls=_entries(("/workspace/data", True))))
    body = client.get("/sandbox-files").json()
    assert fake.calls == [("als", "/workspace")]
    assert body["path"] == "/workspace"
    assert body["parent"] is None


def test_list_caps_entries_and_flags_truncation(monkeypatch):
    _install(
        monkeypatch,
        _FakeBackend(ls=_entries(*[(f"/workspace/f{i:04d}.txt", False) for i in range(900)])),
    )
    body = client.get("/sandbox-files?path=/workspace").json()
    assert len(body["entries"]) == 500
    assert body["truncated"] is True


# --- path safety ------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "/workspace/../etc/passwd",
        "relative/x",
        "/etc",
        "/workspace/..",
        "/work\x00space",
        "/workspacex/secrets",  # prefix-adjacent, not inside the root
    ],
)
def test_list_rejects_paths_outside_root(monkeypatch, bad):
    fake = _install(monkeypatch, _FakeBackend())
    resp = client.get("/sandbox-files", params={"path": bad})
    assert resp.status_code == 400
    assert resp.json()["reason"] == "invalid_path"
    assert fake.calls == []  # rejected before the VM is touched


def test_list_accepts_dotdot_that_normalises_back_inside_root(monkeypatch):
    fake = _install(monkeypatch, _FakeBackend())
    body = client.get("/sandbox-files?path=/workspace/data/../data").json()
    assert body["path"] == "/workspace/data"
    assert fake.calls == [("als", "/workspace/data")]


def test_read_rejects_paths_outside_root(monkeypatch):
    _install(monkeypatch, _FakeBackend())
    resp = client.get("/sandbox-file?path=/etc/passwd")
    assert resp.status_code == 400 and resp.json()["reason"] == "invalid_path"


def test_read_requires_a_path(monkeypatch):
    _install(monkeypatch, _FakeBackend())
    resp = client.get("/sandbox-file")
    assert resp.status_code == 400 and resp.json()["reason"] == "missing_path"


# --- list: error mapping -----------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "status"),
    [("path_not_found", 404), ("not_a_directory", 400), ("permission_denied", 403)],
)
def test_list_maps_backend_errors(monkeypatch, code, status):
    _install(monkeypatch, _FakeBackend(ls=LsResult(error=f"Path '/workspace/x': {code}")))
    resp = client.get("/sandbox-files?path=/workspace/x")
    assert resp.status_code == status
    assert resp.json()["reason"] == code
    assert resp.json()["error"]  # api.ts's errorFrom() reads this


# --- read: text -------------------------------------------------------------------


def _read_ok(content: str, encoding: str = "utf-8") -> ReadResult:
    return ReadResult(file_data=FileData(content=content, encoding=encoding))


def test_read_markdown(monkeypatch):
    fake = _install(monkeypatch, _FakeBackend(read=_read_ok("# Title\nbody")))
    body = client.get("/sandbox-file?path=/workspace/notes/AGENTS.md").json()
    assert body["kind"] == "text"
    assert body["language"] == "markdown"
    assert body["encoding"] == "utf-8"
    assert body["content"] == "# Title\nbody"
    assert body["name"] == "AGENTS.md"
    assert body["truncated"] is False
    assert body["next_offset"] is None
    assert body["offset"] == 0 and body["limit"] == 2000
    assert body["sandbox_id"] == "da-test"
    # limit + 1 on the wire: the extra line is how "page full" is told from "EOF".
    assert fake.calls == [("aread", "/workspace/notes/AGENTS.md", 0, 2001)]


@pytest.mark.parametrize(
    ("name", "language"),
    [("a.py", "py"), ("a.csv", "csv"), ("Dockerfile", None), ("README", None)],
)
def test_read_language_hint(monkeypatch, name, language):
    _install(monkeypatch, _FakeBackend(read=_read_ok("x")))
    body = client.get(f"/sandbox-file?path=/workspace/{name}").json()
    assert body["kind"] == "text" and body["language"] == language


def test_read_clamps_offset_and_limit(monkeypatch):
    fake = _install(monkeypatch, _FakeBackend(read=_read_ok("x")))
    client.get("/sandbox-file?path=/workspace/a.txt&offset=-5&limit=99999")
    assert fake.calls == [("aread", "/workspace/a.txt", 0, 5001)]


def test_read_empty_file_hides_the_reminder(monkeypatch):
    _install(
        monkeypatch,
        _FakeBackend(read=_read_ok("System reminder: File exists but has empty contents")),
    )
    body = client.get("/sandbox-file?path=/workspace/a.txt").json()
    assert body["content"] == "" and body["kind"] == "text"


def test_read_caps_content_bytes(monkeypatch):
    _install(monkeypatch, _FakeBackend(read=_read_ok("a" * (300 * 1024))))
    body = client.get("/sandbox-file?path=/workspace/big.log").json()
    assert len(body["content"]) <= 262144
    assert body["truncated"] is True
    # One line, cut mid-line: paging forward would just re-serve the same bytes.
    assert body["next_offset"] is None


def test_read_reports_in_vm_truncation(monkeypatch):
    _install(
        monkeypatch,
        _FakeBackend(read=_read_ok("a\nb\nc\n\n[Output was truncated due to size limits. …]")),
    )
    body = client.get("/sandbox-file?path=/workspace/a.log").json()
    assert body["truncated"] is True
    # deepagents' operator-facing suffix is never shown as file content.
    assert "[Output was truncated" not in body["content"]
    # "c" was cut mid-line, so the next page restarts at it rather than skipping it.
    assert body["next_offset"] == 2


# --- read: the LINE limit is a truncation too (the in-VM script never flags it) ----


def test_read_flags_a_page_that_filled_the_line_limit(monkeypatch):
    """A 10k-line file under every byte cap must not read as complete."""
    fake = _install(
        monkeypatch,
        _FakeBackend(read=_read_ok("\n".join(f"line{i}" for i in range(2001)))),
    )
    body = client.get("/sandbox-file?path=/workspace/data/sales_full.csv").json()
    assert fake.calls == [("aread", "/workspace/data/sales_full.csv", 0, 2001)]
    assert body["truncated"] is True
    assert body["next_offset"] == 2000
    # The probe line is trimmed, so the client sees exactly the page it asked for.
    assert body["content"].count("\n") + 1 == 2000
    assert body["content"].endswith("line1999")


def test_read_exactly_limit_lines_is_not_truncated(monkeypatch):
    """The +1 probe is what keeps a file of exactly `limit` lines honest."""
    _install(monkeypatch, _FakeBackend(read=_read_ok("\n".join(f"line{i}" for i in range(3)))))
    body = client.get("/sandbox-file?path=/workspace/a.csv&limit=3").json()
    assert body["truncated"] is False
    assert body["next_offset"] is None
    assert body["content"] == "line0\nline1\nline2"


def test_read_next_page_resumes_where_the_last_one_stopped(monkeypatch):
    fake = _install(monkeypatch, _FakeBackend(read=_read_ok("line3\nline4")))
    body = client.get("/sandbox-file?path=/workspace/a.csv&offset=3&limit=3").json()
    assert fake.calls == [("aread", "/workspace/a.csv", 3, 4)]
    assert body["offset"] == 3 and body["truncated"] is False and body["next_offset"] is None


# --- read: 200 placeholders (a state, not an error) --------------------------------


def test_read_binary_extension_never_touches_the_vm(monkeypatch):
    fake = _install(monkeypatch, _FakeBackend(read=_read_ok("should not be reached")))
    resp = client.get("/sandbox-file?path=/workspace/data/chart.png")
    body = resp.json()
    assert resp.status_code == 200
    assert body["kind"] == "binary" and body["content"] is None
    assert body["reason"] == "binary" and ".png" in body["message"]
    assert body["language"] is None and body["encoding"] is None
    assert fake.calls == []  # the allowlist gate fires before any VM call


@pytest.mark.parametrize("name", [".env", ".env.local"])
def test_read_refuses_dotenv(monkeypatch, name):
    fake = _install(monkeypatch, _FakeBackend(read=_read_ok("OPENAI_API_KEY=sk-secret")))
    body = client.get(f"/sandbox-file?path=/workspace/{name}").json()
    assert body["kind"] == "binary" and body["content"] is None
    assert fake.calls == []


def test_read_base64_payload_is_not_leaked(monkeypatch):
    _install(monkeypatch, _FakeBackend(read=_read_ok("QUJD", encoding="base64")))
    resp = client.get("/sandbox-file?path=/workspace/a.txt")
    body = resp.json()
    assert resp.status_code == 200
    assert body["kind"] == "binary" and body["reason"] == "not_previewable"
    assert body["content"] is None
    assert "QUJD" not in resp.text


def test_read_too_large_is_a_placeholder(monkeypatch):
    _install(
        monkeypatch,
        _FakeBackend(
            read=ReadResult(
                error="File '/workspace/a.log': Binary file exceeds maximum preview size of 512000 bytes"
            )
        ),
    )
    resp = client.get("/sandbox-file?path=/workspace/a.log")
    assert resp.status_code == 200
    assert resp.json()["kind"] == "binary" and resp.json()["reason"] == "too_large"


# --- read: error mapping -----------------------------------------------------------


@pytest.mark.parametrize(
    ("detail", "status", "reason"),
    [
        ("file_not_found", 404, "path_not_found"),
        ("not_a_file", 400, "not_a_file"),
        ("permission_denied", 403, "permission_denied"),
        ("Line offset 9 exceeds file length (3 lines)", 416, "bad_offset"),
    ],
)
def test_read_maps_backend_errors(monkeypatch, detail, status, reason):
    _install(monkeypatch, _FakeBackend(read=ReadResult(error=f"File '/workspace/a.md': {detail}")))
    resp = client.get("/sandbox-file?path=/workspace/a.md")
    assert resp.status_code == status
    assert resp.json()["reason"] == reason


# --- backend resolution: key, attach-only, degradation -----------------------------


@pytest.mark.parametrize(
    ("query", "key"),
    [
        ("agent_repo=acme/agents&customer=Acme", "acme/agents"),
        ("agent_repo=&customer=Acme", "Acme"),  # "" must not win — assistant_setup writes it
        ("", "default"),
    ],
)
def test_sandbox_key_mirrors_the_runtime(monkeypatch, query, key):
    seen: list[tuple] = []
    monkeypatch.setattr(A, "_sandbox_enabled", lambda: True)

    def _spy(k, *, create=True):
        seen.append((k, create))
        return _FakeBackend()

    monkeypatch.setattr(A, "_ensure_sandbox", _spy)
    client.get(f"/sandbox-files?{query}")
    assert seen == [(key, False)]  # attach-only: a UI click never provisions a VM


@pytest.mark.parametrize("url", ["/sandbox-files", "/sandbox-file?path=/workspace/a.md"])
def test_sandbox_disabled_degrades_calmly(monkeypatch, url):
    # conftest sets DA_SANDBOX=0 for the whole suite — don't patch _sandbox_enabled.
    called: list = []
    monkeypatch.setattr(A, "_ensure_sandbox", lambda *a, **k: called.append(a))
    resp = client.get(url)
    assert resp.status_code == 503
    assert resp.json()["reason"] == "sandbox_disabled"
    assert called == []  # short-circuits before any sandbox plumbing


@pytest.mark.parametrize("url", ["/sandbox-files", "/sandbox-file?path=/workspace/a.md"])
def test_sandbox_unavailable_degrades_calmly(monkeypatch, url):
    _install(monkeypatch, None)
    resp = client.get(url)
    assert resp.status_code == 503
    assert resp.json()["reason"] == "sandbox_unavailable"


@pytest.mark.parametrize("url", ["/sandbox-files", "/sandbox-file?path=/workspace/a.md"])
def test_no_stack_ever_escapes(monkeypatch, url):
    monkeypatch.setattr(A, "_sandbox_enabled", lambda: True)

    def _boom(key, *, create=True):
        raise RuntimeError("boom")

    monkeypatch.setattr(A, "_ensure_sandbox", _boom)
    resp = client.get(url)
    assert resp.status_code == 500
    assert resp.json()["error"] == "RuntimeError: boom"


@pytest.mark.parametrize("url", ["/sandbox-files", "/sandbox-file?path=/workspace/a.md"])
def test_wedged_vm_times_out(monkeypatch, url):
    import asyncio

    class _Wedged(_FakeBackend):
        async def als(self, path):
            await asyncio.sleep(5)

        async def aread(self, file_path, offset=0, limit=2000):
            await asyncio.sleep(5)

    _install(monkeypatch, _Wedged())
    monkeypatch.setattr(W, "SANDBOX_TIMEOUT", 0.01)
    resp = client.get(url)
    assert resp.status_code == 504
    assert resp.json()["reason"] == "timeout"


def test_wedged_vm_also_times_out_in_the_vm_not_just_the_await(monkeypatch):
    """The 504 above frees the request; only a VM-side timeout frees the THREAD.

    `als`/`aread` reach the VM through `asyncio.to_thread(execute)` on the loop's
    process-wide default executor. Cancelling the awaiting coroutine cannot cancel a
    thread already inside `Sandbox.run`, and `LangSmithSandbox._default_timeout` is
    30 minutes — so what matters is the timeout the route pushes down to `run`.
    """
    calls: list[tuple[str, int | None]] = []

    class _Recording(_FakeRawSandbox):
        def run(self, command, timeout=None):
            calls.append(("run", timeout))
            return _FakeRun(self._stdout)

    backend = LangSmithSandbox(_Recording('{"encoding": "utf-8", "content": "hi"}'))
    _install(monkeypatch, backend)
    monkeypatch.setattr(W, "SANDBOX_TIMEOUT", 7)

    client.get("/sandbox-files?path=/workspace")
    client.get("/sandbox-file?path=/workspace/a.md")

    assert calls == [("run", 7), ("run", 7)]  # not 1800 — the thread comes back
    # And the backend the AGENT runs turns on keeps its generous default.
    assert backend._default_timeout == 30 * 60


# --- wire fidelity: REAL LangSmithSandbox parsers over canned VM stdout ------------


class _FakeRun:
    def __init__(self, stdout=""):
        self.stdout, self.stderr, self.exit_code = stdout, "", 0


class _FakeRawSandbox:
    """Stand-in for a langsmith `Sandbox` — replays canned in-VM script output."""

    def __init__(self, stdout: str):
        self.name = "da-wire"
        self._stdout = stdout

    def run(self, command: str, timeout: int | None = None) -> _FakeRun:
        return _FakeRun(self._stdout)


def test_list_consumes_real_lsresult(monkeypatch):
    stdout = (
        '{"path": "/workspace/a.md", "is_dir": false}\n'
        '{"path": "/workspace/data", "is_dir": true}\n'
    )
    _install(monkeypatch, LangSmithSandbox(_FakeRawSandbox(stdout)))
    body = client.get("/sandbox-files?path=/workspace").json()
    assert [(e["name"], e["kind"]) for e in body["entries"]] == [("data", "dir"), ("a.md", "text")]
    assert body["sandbox_id"] == "da-wire"


def test_read_consumes_real_readresult(monkeypatch):
    _install(
        monkeypatch,
        LangSmithSandbox(_FakeRawSandbox('{"encoding": "utf-8", "content": "# Title"}')),
    )
    body = client.get("/sandbox-file?path=/workspace/a.md").json()
    assert body["content"] == "# Title" and body["language"] == "markdown"


# --- agent.py contract: create=False is attach-only --------------------------------


class _FakeVM:
    def __init__(self, name):
        self.name, self.runs = name, []

    def run(self, command, timeout=None):
        self.runs.append(command)
        return _FakeRun("ok")


class _FakeClient:
    def __init__(self, existing=()):
        self.created: list[str] = []
        self.existing = list(existing)

    def list_sandboxes(self, **_):
        return list(self.existing)

    def create_sandbox(self, *, name=None, **_):
        self.created.append(name or "unnamed")
        sb = _FakeVM(name or "unnamed")
        self.existing.append(sb)
        return sb


def test_ensure_sandbox_create_false_never_provisions(monkeypatch):
    ls_client = _FakeClient()
    monkeypatch.setenv("DA_SANDBOX", "1")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")
    monkeypatch.setattr(A, "SandboxClient", lambda **kw: ls_client)
    assert A._ensure_sandbox("acme", create=False) is None
    assert ls_client.created == []  # no VM, no ~30s boot, no pip install
    assert A._SANDBOX_CACHE == {}  # and nothing poisoned the cache


def test_ensure_sandbox_create_false_reattaches_existing(monkeypatch):
    ls_client = _FakeClient(existing=[_FakeVM("da-acme")])
    monkeypatch.setenv("DA_SANDBOX", "1")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")
    monkeypatch.setattr(A, "SandboxClient", lambda **kw: ls_client)
    backend = A._ensure_sandbox("acme", create=False)
    assert isinstance(backend, LangSmithSandbox)
    assert ls_client.created == []
    assert [c for vm in ls_client.existing for c in vm.runs] == []  # attach ⇒ no re-seed


def test_ensure_sandbox_still_creates_by_default(monkeypatch):
    ls_client = _FakeClient()
    monkeypatch.setenv("DA_SANDBOX", "1")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")
    monkeypatch.setattr(A, "SandboxClient", lambda **kw: ls_client)
    assert A._ensure_sandbox("acme") is not None
    assert ls_client.created == ["da-acme"]  # existing callers keep today's behaviour
