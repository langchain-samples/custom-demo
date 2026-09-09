"""The assistant's code-execution VM, as three routes over HTTP.

`GET /sandbox-files` lists one directory, `GET /sandbox-file` reads one file, and
`POST /sandbox-upload` writes files in. Everything is bounded: a wedged VM must not
hold an HTTP request, and a 2 GB log in /workspace must not travel through the Agent
Server process. The naming, path-confinement and page-clipping rules live next door in
`web/files.py`; what is here is the plumbing that actually touches the VM.

All three are ATTACH-ONLY. A cold start is a ~30s boot plus a pip install, which would
outlive any of these requests, so a toolbar click never provisions a VM.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import posixpath

from starlette.responses import JSONResponse

from custom_demo.config import load_env
from custom_demo.resources.sandbox import attach_sandbox, sandbox_enabled, sandbox_key_from
from custom_demo.web.errors import err, route_error
from custom_demo.web.files import (
    EMPTY_FILE_REMINDER,
    MEDIA_MIME,
    clip_page,
    extension,
    file_kind,
    files_root,
    language_of,
    safe_path,
    upload_name,
)

# One directory page. deepagents gives no cap of its own, and a pip target dir can
# hold tens of thousands of entries.
_MAX_ENTRIES = 500

# Module-level so tests can shrink it. Applied TWICE per VM call: once as the
# request deadline (asyncio.wait_for), once as the in-VM command timeout
# (`_bounded`).
SANDBOX_TIMEOUT = 20

# Ceiling on VM calls in flight from these routes. `asyncio.to_thread` runs on
# the loop's process-wide default executor (min(32, cpu+4) workers) that
# langgraph_api's own serde/store code also uses, so an unresponsive VM must not be
# able to starve it however many times the user clicks.
_MAX_INFLIGHT = 4
_SANDBOX_SLOTS = asyncio.Semaphore(_MAX_INFLIGHT)

# Ceiling on a media file, in BYTES of the original. The in-VM read caps stdout at
# ~500 KiB and base64 inflates by 4/3, so this is what fits in one round trip with room
# to spare. A generated demo PDF is a few KB; this is only a guard against someone
# uploading a scanned manual.
_MAX_MEDIA_BYTES = 300 * 1024

# Where uploads land by default: the directory the prompt tells the agent to look in.
_UPLOAD_DIR = "/workspace/data"

# Per-request caps. Generous enough for a scanned PDF deck, small enough that a
# mis-drag cannot wedge the deployment on a base64 decode.
_MAX_UPLOAD_FILES = 5
_MAX_UPLOAD_BYTES = 15 * 1024 * 1024

_LS_ERROR_STATUS = {"path_not_found": 404, "not_a_directory": 400, "permission_denied": 403}
_READ_ERROR_STATUS = {
    # The read script says "file_not_found"; the SPA branches on one slug for both routes.
    "file_not_found": ("path_not_found", 404),
    "not_a_file": ("not_a_file", 400),
    "permission_denied": ("permission_denied", 403),
}

_TIMED_OUT = ("timeout", "The sandbox did not respond.")


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


async def _resolve_backend(request, params: dict | None = None):
    """(backend, None) for this assistant's VM, or (None, JSONResponse) explaining why not.

    `params` overrides the query string, for POST routes that carry the keys in a JSON
    body instead.

    Keyed by sandbox_key, with agent_repo/customer fallbacks shared with runtime so the
    browser sees the SAME VM a chat turn warmed — and, because this app and the
    graph share one process, usually straight out of `_SANDBOX_CACHE` with no network.
    """
    load_env()  # `sandbox_enabled()` reads os.getenv directly and never loads .env itself
    if not sandbox_enabled():
        return None, err(
            503, "sandbox_disabled", "Agent file access is turned off for this deployment."
        )

    # provisioning/setup.py writes `ls_artifacts.agent_repo = ""` when there is no Context Hub
    # repo, and the SPA forwards it verbatim → coerce "" to None like the runtime's `or`.
    source = params if params is not None else request.query_params
    agent_repo = source.get("agent_repo") or None
    customer = source.get("customer") or None
    # The assistant's own VM name when it has one, so the browser opens the same VM the
    # agent talks to rather than whatever VM the customer name resolves to.
    sandbox_key = source.get("sandbox_key") or None
    # attach-only: a toolbar click must never provision a VM (~30s boot + pip install).
    # Sync + network → to_thread so it can't block the event loop.
    backend = await asyncio.to_thread(
        attach_sandbox, sandbox_key_from(sandbox_key, agent_repo, customer)
    )
    if backend is None:
        return None, err(503, "sandbox_unavailable", "No sandbox files for this assistant.")

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


# --- GET /sandbox-files ----------------------------------------------------------


def _dir_entry(info: dict) -> dict | None:
    """One `entries[]` row from a scandir record, or None for a record with no path.

    `kind` is extension-derived, so the UI can grey out non-previewable files BEFORE
    the user clicks (no size/mtime: the scandir script emits only {path, is_dir}).
    """
    path = info.get("path") or ""
    if not path:
        return None

    name = posixpath.basename(path)
    is_dir = bool(info.get("is_dir"))
    return {
        "name": name,
        "path": path,
        "is_dir": is_dir,
        "kind": "dir" if is_dir else file_kind(name),
    }


@route_error
async def sandbox_files(request):
    """List ONE directory of the assistant's sandbox VM (lazy, expand-on-click).

    GET ?path=<abs, default root>&agent_repo=&customer= → {root, path, parent,
    entries[{name, path, is_dir, kind}], truncated, sandbox_id}.

    Lazy rather than recursive: `als` and `aglob` cost the same single VM round trip,
    but `aglob("**/*")` has no depth or entry cap and its output is silently truncated
    mid-JSON-line at the VM's stdout limit — a stray `node_modules` would return half a
    tree with no error flag. One directory per request has an honest bound.
    """
    root = files_root()
    path = safe_path(request.query_params.get("path") or root, root)
    if path is None:
        return err(400, "invalid_path", f"path must be an absolute path inside {root}")

    backend, failure = await _resolve_backend(request)
    if failure is not None:
        return failure

    async def _ls():
        # Bounded twice on purpose: the wait_for below is the request deadline,
        # `_bounded` is the in-VM command timeout. See SANDBOX_TIMEOUT.
        async with _SANDBOX_SLOTS:
            return await _bounded(backend).als(path)

    try:
        res = await asyncio.wait_for(_ls(), timeout=SANDBOX_TIMEOUT)
    except TimeoutError:
        return err(504, *_TIMED_OUT)

    if res.error:
        # deepagents prefixes "Path '<path>': " — match on the suffix code only.
        code = res.error.rsplit(": ", 1)[-1]
        status = _LS_ERROR_STATUS.get(code)
        return err(status, code, res.error) if status else err(500, "internal", res.error)

    entries = [entry for info in res.entries or [] if (entry := _dir_entry(info))]
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


# --- GET /sandbox-file -----------------------------------------------------------


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


async def _vm_media(backend, path: str) -> tuple[str, str]:
    """Base64 of a small binary file, as `(payload, error)` - exactly one is non-empty.

    `aread` is line-oriented and would mangle bytes, so this shells out. The size check
    runs IN THE VM and before the encode, so an oversized file costs one cheap round trip
    instead of half a megabyte of base64 that the caller then throws away.

    Single-quoted path: it comes from `safe_path`, so it is already confined to the
    sandbox root, and quoting keeps a space or bracket in a filename from splitting.
    """
    quoted = "'" + path.replace("'", "'\\''") + "'"
    script = (
        f"sz=$(stat -c %s {quoted} 2>/dev/null || echo -1)\n"
        f'if [ "$sz" -lt 0 ]; then echo "ERR:missing";\n'
        f'elif [ "$sz" -gt {_MAX_MEDIA_BYTES} ]; then echo "ERR:too_large:$sz";\n'
        f"else base64 -w0 {quoted}; fi"
    )
    async with _SANDBOX_SLOTS:
        out = await _bounded(backend).aexecute(script)

    text = (getattr(out, "result", None) or getattr(out, "stdout", None) or str(out)).strip()
    if text.startswith("ERR:"):
        return "", text[4:]

    return text, ""


async def _media_page(backend, path: str, base: dict) -> JSONResponse:
    """A browser-renderable binary as base64, or a 200 placeholder saying why not.

    Shipped as bytes rather than refused: a browser renders a PDF or an image natively,
    and a claims demo whose denial letter cannot be opened is missing the document the
    whole scenario is about.
    """
    ext = extension(base["name"])
    try:
        payload, problem = await asyncio.wait_for(_vm_media(backend, path), timeout=SANDBOX_TIMEOUT)
    except TimeoutError:
        return err(504, *_TIMED_OUT)
    except Exception:  # noqa: BLE001 - a backend without `aexecute`, or a VM that
        # refused the command. A preview pane that 500s is worse than one that says
        # it cannot show the file, and every other unshowable case here is a 200.
        payload, problem = "", "unreadable"

    if problem.startswith("too_large"):
        return _placeholder(base, "too_large", f"This {ext.upper()} is too large to preview.")

    if problem or not payload:
        return _placeholder(base, "binary", f"Could not read this {ext.upper()}.")

    return JSONResponse(
        {
            **base,
            "kind": "media",
            "language": ext,
            "encoding": "base64",
            "mime": MEDIA_MIME[ext],
            "content": payload,
            "truncated": False,
            "next_offset": None,
        }
    )


def _read_failure(error: str, base: dict) -> JSONResponse:
    """What a failed in-VM read means to the viewer: a placeholder, or a status."""
    if "exceeds maximum preview size" in error:
        return _placeholder(base, "too_large", "File is too large to preview.")

    if "exceeds file length" in error:
        return err(416, "bad_offset", error)

    mapped = _READ_ERROR_STATUS.get(error.rsplit(": ", 1)[-1])
    if mapped is None:
        return err(500, "internal", error)

    return err(mapped[1], mapped[0], error)


async def _text_page(backend, path: str, base: dict) -> JSONResponse:
    """One page of a UTF-8 text file, or the placeholder/status that stands in for it."""
    offset, limit = base["offset"], base["limit"]

    async def _read():
        # `aread`, NOT `read`: LangSmithSandbox overrides only the SYNC read, which
        # downloads the ENTIRE file into this process before paginating (a big log
        # would OOM the deployment). `aread` runs the in-VM script — offset/limit
        # applied there, stdout capped at ~500 KiB. Do not "simplify" to to_thread(read).
        #
        # `limit + 1`: the in-VM script stops at `limit` lines WITHOUT flagging
        # it (it sets its own marker only when the ~500 KiB stdout cap blows), so
        # the extra line is the only way to tell "the file ends here" from "the
        # page filled up". It is trimmed off by `clip_page` and never reaches the client.
        async with _SANDBOX_SLOTS:
            return await _bounded(backend).aread(path, offset, limit + 1)

    try:
        res = await asyncio.wait_for(_read(), timeout=SANDBOX_TIMEOUT)
    except TimeoutError:
        return err(504, *_TIMED_OUT)

    if res.error:
        return _read_failure(res.error, base)

    data = res.file_data or {}
    if data.get("encoding") != "utf-8":
        # A text-extension file whose bytes failed the in-VM UTF-8 sniff. Never
        # ship the base64 blob — nothing can render it.
        return _placeholder(
            base, "not_previewable", "Not a UTF-8 text file - preview not supported."
        )

    content = data.get("content") or ""
    if content == EMPTY_FILE_REMINDER:
        content = ""

    content, whole_lines, truncated = clip_page(content, limit)
    return JSONResponse(
        {
            **base,
            "kind": "text",
            "language": language_of(base["name"]),
            "encoding": "utf-8",
            "content": content,
            "truncated": truncated,
            # Where a "Show more" click resumes. None when this is the last page
            # (or when a single line is itself over a cap, so paging can't advance).
            "next_offset": offset + whole_lines if truncated and whole_lines else None,
        }
    )


@route_error
async def sandbox_file(request):
    """Read ONE file from the assistant's sandbox VM, paginated by line.

    GET ?path=<abs, required>&offset=0&limit=2000&agent_repo=&customer= → a text body
    {kind:"text", language, encoding, content, offset, limit, truncated, next_offset,
    sandbox_id}, or a 200 placeholder {kind:"binary", reason, message, content:null}.

    `truncated` means "this is not the whole file"; `next_offset` is the line to ask
    for next (null on the last page), which is what the viewer's "Show more" sends.
    """
    root = files_root()
    raw = request.query_params.get("path")
    if not raw:
        return err(400, "missing_path", "path is required")

    path = safe_path(raw, root)
    if path is None:
        return err(400, "invalid_path", f"path must be an absolute path inside {root}")

    backend, failure = await _resolve_backend(request)
    if failure is not None:
        return failure

    name = posixpath.basename(path)
    base = {
        "path": path,
        "name": name,
        "offset": max(0, _int_param(request, "offset", 0)),
        "limit": min(5000, max(1, _int_param(request, "limit", 2000))),
        "sandbox_id": _sandbox_id(backend),
    }
    kind = file_kind(name)
    if kind == "media":
        return await _media_page(backend, path, base)

    if kind == "text":
        return await _text_page(backend, path, base)

    label = posixpath.splitext(name)[1] or name
    return _placeholder(base, "binary", f"Binary file ({label}) - preview not supported.")


# --- POST /sandbox-upload --------------------------------------------------------
#
# The demo's missing half: the agent could always READ its VM, but a presenter had no
# way to put anything IN it. So an assistant built for a document use case would ask
# for the PDF it needs and then have nowhere to receive it — a dead end that reads as a
# broken demo. This is the channel that makes "here is a real customer document" work.
#
# Base64 in a JSON body rather than multipart: starlette's `request.form()` needs
# python-multipart, which is not a dependency here, and a new one is not worth it for
# a handful of files a presenter drags in by hand.


def _decode_uploads(files: list, target: str) -> tuple[list[tuple[str, bytes]], list[dict]]:
    """Split the posted files into `(writable, rejected)`. Caps enforced HERE, not in the browser."""
    decoded: list[tuple[str, bytes]] = []
    failed: list[dict] = []
    for item in files:
        raw_name = (item or {}).get("name") if isinstance(item, dict) else None
        name = upload_name(str(raw_name or ""))
        if name is None:
            failed.append({"name": str(raw_name or ""), "error": "invalid_name"})
            continue

        try:
            content = base64.b64decode(str((item or {}).get("content_b64") or ""), validate=True)
        except Exception:  # noqa: BLE001 - a bad payload is the caller's problem, not a 500
            failed.append({"name": name, "error": "invalid_base64"})
            continue

        if not content:
            failed.append({"name": name, "error": "empty"})
        elif len(content) > _MAX_UPLOAD_BYTES:
            failed.append({"name": name, "error": "too_large"})
        else:
            decoded.append((posixpath.join(target, name), content))

    return decoded, failed


@route_error
async def sandbox_upload(request):
    """Write files into the assistant's sandbox VM.

    POST {sandbox_key?, agent_repo?, customer?, dir?, files:[{name, content_b64}]} →
    {dir, written:[{name, path}], failed:[{name, error}], sandbox_id}.

    Attach-only, like the two read routes: it uses the VM a chat turn or the setup
    prewarm already created, and never provisions one. A cold start is a ~30s boot plus
    a pip install, which would outlive this request — so an assistant whose VM is gone
    is told to send a message first rather than left hanging.
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - an unreadable body is a client error; answer 400, not 500
        return err(400, "invalid_body", "Body must be JSON.")

    if not isinstance(body, dict):
        return err(400, "invalid_body", "Body must be a JSON object.")

    files = body.get("files")
    if not isinstance(files, list) or not files:
        return err(400, "no_files", "Send at least one file.")

    if len(files) > _MAX_UPLOAD_FILES:
        return err(413, "too_many_files", f"At most {_MAX_UPLOAD_FILES} files per upload.")

    root = files_root()
    target = safe_path(str(body.get("dir") or _UPLOAD_DIR), root)
    if target is None:
        return err(400, "invalid_path", f"dir must be an absolute path inside {root}")

    decoded, failed = _decode_uploads(files, target)
    if not decoded:
        return JSONResponse({"dir": target, "written": [], "failed": failed}, status_code=400)

    backend, failure = await _resolve_backend(request, params=body)
    if failure is not None:
        return failure

    try:
        async with _SANDBOX_SLOTS:
            results = await asyncio.wait_for(
                asyncio.to_thread(backend.upload_files, decoded),
                timeout=SANDBOX_TIMEOUT,
            )
    except TimeoutError:
        return err(504, *_TIMED_OUT)

    written: list[dict] = []
    for res in results or []:
        path = str(res.path or "")
        entry = {"name": posixpath.basename(path), "path": path}
        if res.error:
            failed.append({**entry, "error": str(res.error)})
        else:
            written.append(entry)

    return JSONResponse(
        {"dir": target, "written": written, "failed": failed, "sandbox_id": _sandbox_id(backend)}
    )
