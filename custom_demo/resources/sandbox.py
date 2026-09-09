"""Assistant-scoped sandbox identity, seed data, acquisition and retention."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

from deepagents.backends import LangSmithSandbox
from langsmith.sandbox import SandboxClient as _LangSmithSandboxClient

from custom_demo.config import sandbox_enabled as sandbox_configured


@dataclass
class _SandboxEntry:
    """A cached assistant VM and the monotonic time it was last validated."""

    backend: Any
    validated_at: float


_SANDBOX_CACHE: dict[str, _SandboxEntry] = {}

_SANDBOX_IDLE_TTL = 3600
_SANDBOX_DELETE_AFTER_STOP = 7 * 24 * 3600
_SANDBOX_REVALIDATE_AFTER = 600

SandboxClient: Any = _LangSmithSandboxClient

_SEED_INSTALL = (
    "pip install --break-system-packages -q pandas numpy statsmodels scikit-learn pypdf "
    ">/dev/null 2>&1 || true\n"
)

SEED_MAX_FILES = 4
_SEED_MAX_ROWS = 40
_SEED_MAX_TEXT = 8000
_SEED_KINDS = frozenset({"csv", "json", "txt", "md", "pdf"})


def _seed_file_name(raw: str) -> str:
    """Sanitize an untrusted seed filename to a basename within /workspace/data."""
    name = os.path.basename((raw or "").strip().replace("\\", "/"))
    if not name or name in {".", ".."} or name.lower().startswith(".env"):
        return ""

    return "".join(c for c in name if c.isalnum() or c in "._- ").strip() or ""


def render_seed_script(files: list[dict]) -> str:
    """A shell script that writes `files` into /workspace/data. "" if there is nothing.

    Deterministic: the model supplies data, this supplies the code. Everything is passed
    to the VM as a JSON document and written by a fixed python heredoc, so no
    model-authored text is ever interpolated into shell.

    PDFs need a library the base image lacks, so `fpdf2` is installed for them and a
    failed install downgrades that file to `.txt` rather than losing its content — an
    unreadable demo document is worse than a plainly readable one.
    """
    clean: list[dict] = []
    for spec in files[:SEED_MAX_FILES]:
        if not isinstance(spec, dict):
            continue

        name = _seed_file_name(str(spec.get("name") or ""))
        kind = str(spec.get("kind") or "").lower().lstrip(".")
        if not name or kind not in _SEED_KINDS:
            continue

        rows = [
            [str(cell) for cell in row]
            for row in (spec.get("rows") or [])[:_SEED_MAX_ROWS]
            if isinstance(row, list)
        ]
        clean.append(
            {
                "name": name,
                "kind": kind,
                "columns": [str(c) for c in (spec.get("columns") or [])],
                "rows": rows,
                "text": str(spec.get("text") or "")[:_SEED_MAX_TEXT],
            }
        )

    if not clean:
        return ""

    payload = json.dumps({"files": clean}, ensure_ascii=True)
    wants_pdf = any(f["kind"] == "pdf" for f in clean)
    install = (
        "pip install --break-system-packages -q fpdf2 >/dev/null 2>&1 || true\n"
        if wants_pdf
        else ""
    )
    return (
        _SEED_INSTALL
        + install
        + "mkdir -p /workspace/data\n"
        + "cat > /tmp/seed.json <<'SPEC'\n"
        + payload
        + "\nSPEC\n"
        + _SEED_WRITER
    )


_SEED_WRITER = """python3 - <<'PY'
import csv, json, pathlib
spec = json.loads(pathlib.Path("/tmp/seed.json").read_text())
out = pathlib.Path("/workspace/data")
out.mkdir(parents=True, exist_ok=True)
for f in spec["files"]:
    path = written = out / f["name"]
    try:
        if path.exists() or path.with_suffix(".txt").exists():
            # Never overwrite: whatever is on disk wins, including a file the user
            # uploaded into /workspace/data under a seed file's name.
            print("kept", path)
            continue
        if f["kind"] == "csv":
            with path.open("w", newline="") as fh:
                w = csv.writer(fh)
                if f["columns"]:
                    w.writerow(f["columns"])
                w.writerows(f["rows"])
        elif f["kind"] == "json":
            cols = f["columns"]
            records = [dict(zip(cols, row)) for row in f["rows"]] if cols else f["rows"]
            path.write_text(json.dumps(records, indent=2))
        elif f["kind"] == "pdf":
            try:
                from fpdf import FPDF

                pdf = FPDF()
                pdf.add_page()
                pdf.set_font("Helvetica", size=11)
                for line in f["text"].splitlines() or [""]:
                    # new_x/new_y are load-bearing: multi_cell(w=0) defaults to leaving
                    # the cursor at the RIGHT margin, so a second line has zero width
                    # and fpdf raises "Not enough horizontal space to render a single
                    # character". Return to the left margin and step down instead.
                    pdf.multi_cell(0, 6, line, new_x="LMARGIN", new_y="NEXT")
                pdf.output(str(path))
                written = path
            except Exception as exc:
                # Downgrade rather than lose the document, but SAY SO. A silent
                # .pdf -> .txt is how a document demo ends up quietly not being one.
                print("pdf unavailable, wrote text instead:", type(exc).__name__, exc)
                written = path.with_suffix(".txt")
                written.write_text(f["text"])
        else:
            path.write_text(f["text"])
        print("seeded", written)
    except Exception as exc:
        print("seed failed", path, exc)
PY"""


def _slug(text: str) -> str:
    """A DNS-ish sandbox-name slug from an arbitrary id."""
    out = "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")
    return out or "default"


_KEY_SOURCE_REPORTED: set[tuple[str, str]] = set()


def sandbox_key_from(
    sandbox_key: str | None, agent_repo: str | None = None, customer: str | None = None
) -> str:
    """Resolve the assistant key, falling back to agent repo, customer, then default.

    Customer-derived fallbacks are shared across assistants and logged once per key.
    New assistants receive a unique sandbox_key at setup.
    """
    if sandbox_key:
        return sandbox_key

    if agent_repo:
        source, key = "agent_repo", agent_repo
    elif customer:
        source, key = "customer name", customer
    else:
        source, key = "neither, so the process-wide shared", "default"

    if (source, key) not in _KEY_SOURCE_REPORTED:
        _KEY_SOURCE_REPORTED.add((source, key))
        print(
            f"[sandbox] this assistant has no sandbox_key of its own, so it attaches to "
            f"the VM named for its {source} ({key!r}). Any other assistant resolving to "
            f"the same name SHARES that VM and its files, and skips its own seed."
        )

    return key


def _sandbox_key_credentials() -> tuple[str | None, dict[str, str]]:
    """Read sandbox credentials and hosting workspace from deployment configuration.

    These credentials are independent of the assistant's Context Hub workspace.
    The SDK needs X-Tenant-Id for org-scoped control-plane requests; data-plane
    file operations require a workspace-scoped key.
    """
    api_key = os.getenv("LANGSMITH_API_KEY") or os.getenv("LS_CROSS_WORKSPACE_KEY") or None
    workspace = os.getenv("LANGSMITH_WORKSPACE_ID") or os.getenv("WORKSPACE_ID") or ""
    return api_key, {"X-Tenant-Id": workspace} if workspace else {}


def sandbox_enabled() -> bool:
    """Whether deployment configuration and credentials permit sandbox acquisition."""
    if SandboxClient is None or not sandbox_configured():
        return False

    return bool(_sandbox_key_credentials()[0])


class SeedSpecError(RuntimeError):
    """An assistant has no usable starting-files spec; no substitute dataset is allowed."""


def seed_script_or_raise(seed: list[dict] | None) -> str:
    """Render seed data or raise SeedSpecError naming the missing or invalid spec.

    Runtime validates before acquiring a VM; provisioning also validates at seed time.
    """
    script = render_seed_script(seed or [])
    if script:
        return script

    supplied = len(seed) if isinstance(seed, list) else 0
    detail = (
        f"none of the {supplied} entries in its `sandbox_seed` spec is a usable file "
        f"(each needs a name and a kind from {sorted(_SEED_KINDS)})"
        if supplied
        else "its stored context carries no `sandbox_seed` spec at all, so setup either "
        "predates per-assistant seed files or did not produce any"
    )
    raise SeedSpecError(
        f"This assistant has no starting files to plant in /workspace/data: {detail}. "
        "Re-run setup for it rather than answering from another assistant's data."
    )


def _seed_data(backend: Any, seed: list[dict] | None = None) -> None:
    """Seed a newly created VM with its assistant's data.

    Invalid data raises SeedSpecError. VM transport failures are logged and do not
    abort the run. Never call this while attaching to an existing VM.
    """
    script = seed_script_or_raise(seed)
    try:
        backend.execute(script)
    except Exception as exc:  # noqa: BLE001 - a VM/transport failure must not fail the run
        print(f"[sandbox] seeding failed: {type(exc).__name__}: {exc}")


_SANDBOX_WAIT_SECONDS = 25.0
_SANDBOX_POLL_SECONDS = 1.5


def _status_or_none(client: Any, name: str) -> str | None:
    """Read VM status, returning None if the service cannot determine it."""
    try:
        status = client.get_sandbox_status(name)
    except Exception:  # noqa: BLE001 - gone, unreachable, or an SDK without the call
        return None

    value = str(getattr(status, "status", "") or "").lower()
    return value or None


def _wait_ready(client: Any, name: str, seconds: float = _SANDBOX_WAIT_SECONDS) -> bool:
    """Poll until ready or the deadline expires; an unavailable status API permits use."""
    deadline = time.monotonic() + seconds
    while True:
        status = _status_or_none(client, name)
        if status is None or status == "ready":
            return True

        if time.monotonic() >= deadline:
            return False

        time.sleep(_SANDBOX_POLL_SECONDS)


def _acquire_raw(client: Any, name: str, *, create: bool) -> tuple[Any, bool] | None:
    """The live VM called `name`: restarted if stopped, created if absent.

    Returns `(raw_sandbox, created)`, or None when nothing can be attached to and
    `create` is False. `created` is True only for a brand-new VM — the only case that
    needs seeding, since a restarted one still has the filesystem it was stopped with.
    """
    raw = next((s for s in client.list_sandboxes() if s.name == name), None)
    if raw is not None:
        if str(getattr(raw, "status", "") or "").lower() != "stopped":
            return raw, False

        try:
            return client.start_sandbox(name) or raw, False
        except Exception:  # noqa: BLE001 - unstartable is as good as absent
            pass

    if not create:
        return None

    return (
        client.create_sandbox(
            name=name,
            idle_ttl_seconds=_SANDBOX_IDLE_TTL,
            delete_after_stop_seconds=_SANDBOX_DELETE_AFTER_STOP,
        ),
        True,
    )


def attach_sandbox(key: str) -> Any | None:
    """Attach to an existing VM, restarting it if stopped; never create or seed one."""
    return _acquire_sandbox(key, create=False, seed=None)


def ensure_sandbox(key: str, *, seed: list[dict] | None) -> Any | None:
    """Reuse an assistant's VM, or create and seed it if none can be attached to."""
    return _acquire_sandbox(key, create=True, seed=seed)


def _acquire_sandbox(key: str, *, create: bool, seed: list[dict] | None) -> Any | None:
    """Resolve a cached VM under the caller's explicit provisioning policy.

    Transport failures return None. Invalid seed data raises SeedSpecError instead
    of leaving the caller to substitute unrelated data for the assistant's files.
    """
    cached = _SANDBOX_CACHE.get(key)
    now = time.monotonic()
    if cached is not None and now - cached.validated_at < _SANDBOX_REVALIDATE_AFTER:
        return cached.backend

    try:
        return _revalidate_or_acquire(key, cached, now, create=create, seed=seed)
    except SeedSpecError:
        raise
    except Exception:  # noqa: BLE001 - callers handle unavailable infrastructure
        return None


def _revalidate_or_acquire(
    key: str, cached: _SandboxEntry | None, now: float, *, create: bool, seed: list[dict] | None
) -> Any | None:
    """Revalidate a stale entry, then attach or provision according to caller intent."""
    api_key, headers = _sandbox_key_credentials()
    client = SandboxClient(api_key=api_key, headers=headers or None)
    name = f"da-{_slug(key)}"
    if cached is not None and _status_or_none(client, name) == "ready":
        cached.validated_at = now
        return cached.backend

    _SANDBOX_CACHE.pop(key, None)
    got = _acquire_raw(client, name, create=create)
    if got is None:
        return None

    raw, created = got
    if not _wait_ready(client, name):
        return None

    backend = LangSmithSandbox(raw)
    if created:
        _seed_data(backend, seed)

    _SANDBOX_CACHE[key] = _SandboxEntry(backend=backend, validated_at=now)
    return backend


def prewarm_sandbox(
    agent_repo: str | None = None,
    customer: str | None = None,
    seed: list[dict] | None = None,
    sandbox_key: str | None = None,
) -> None:
    """Prepare the same assistant VM used by turns and file access.

    Setup calls this in the background. Disabled infrastructure is a no-op and
    failures are logged rather than propagated to assistant creation.
    """
    if not sandbox_enabled():
        return

    try:
        ensure_sandbox(sandbox_key_from(sandbox_key, agent_repo, customer), seed=seed)
    except Exception as exc:  # noqa: BLE001 - provisioning must never fail on a warm-up
        print(f"[sandbox] prewarm failed: {type(exc).__name__}: {exc}")
