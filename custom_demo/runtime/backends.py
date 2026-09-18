"""Adapt per-run configuration to assistant resources and Context Hub filesystems.

The resource layer owns VM lifetime. This module owns only the execution topology:
a sandbox default exposes execute, a /skills/ route exposes the Hub bundle, and the
artifacts directory routes to a Hub documents repo when the assistant names one.
"""

from __future__ import annotations

import contextlib
from typing import Any

from deepagents.backends import CompositeBackend, ContextHubBackend, StateBackend
from deepagents.backends.protocol import BackendProtocol, DeleteResult, EditResult, WriteResult
from langgraph.runtime import get_runtime
from langsmith.utils import LangSmithConflictError

from custom_demo.config import scoped_client
from custom_demo.core.ctx import Context, get_ctx
from custom_demo.resources import sandbox


def _get_or_create_sandbox(runtime) -> Any | None:
    """Resolve this run's assistant VM, validating its data before acquisition."""
    if not sandbox.sandbox_enabled():
        return None

    ctx = get_ctx(runtime)
    spec = ctx.sandbox_seed
    # Validate before acquisition on every turn, not only when a new VM is seeded.
    sandbox.seed_script_or_raise(spec)
    key = sandbox.sandbox_key_from(ctx.sandbox_key, ctx.agent_repo, ctx.customer)
    return sandbox.ensure_sandbox(key, seed=spec)


def turn_blocking_failure(ctx: Context) -> str | None:
    """The sentence a turn has to answer with when this assistant cannot be set up, else None.

    The same validation `_get_or_create_sandbox` runs, and the same refusal: an
    assistant with no `sandbox_seed` spec still gets no VM and no substitute dataset.
    What this adds is a place to ask BEFORE the graph starts. The first middleware to
    touch the filesystem resolves the backend from inside `before_agent`, where a raise
    leaves the whole run with `outputs: null` and a traceback, so the presenter sees an
    unanswered turn rather than the sentence naming what is misconfigured. `graph.py`
    reads this per run and answers with it instead.
    """
    if not sandbox.sandbox_enabled():
        return None

    try:
        sandbox.seed_script_or_raise(ctx.sandbox_seed)
    except sandbox.SeedSpecError as exc:
        return str(exc)

    return None


# Prompt and skills repos only. A ContextHubBackend loads the repo's whole tree on first
# access and then holds it, which is right for content that changes at provisioning time
# and wrong for anything with a second writer. Documents have one: see `_docs_route`.
_CTXHUB_CACHE: dict[tuple[str, str | None], Any] = {}


def _ctxhub_backend(repo: str, ws: str | None) -> Any:
    """Reuse the workspace-scoped Hub backend across filesystem operations."""
    key = (repo, ws)
    backend = _CTXHUB_CACHE.get(key)
    if backend is None:
        backend = ContextHubBackend(repo, client=scoped_client(ws))
        _CTXHUB_CACHE[key] = backend

    return backend


# Where the agent writes documents, and the mount the documents repo answers for. Shared
# with the SPA's `frontend/src/lib/artifacts.ts:ARTIFACT_DIR`, which decides the same paths
# deserve their own tab. CompositeBackend strips this prefix, so a document written to
# `/workspace/artifacts/req-204/brief.md` is stored in the repo as `req-204/brief.md`.
ARTIFACTS_MOUNT = "/workspace/artifacts/"


def _unconfirmed(file_path: str, exc: Exception) -> str:
    """The tool-result text for a documents change the Hub never confirmed."""
    return (
        f"The documents repo did not confirm the change to {file_path}: "
        f"{type(exc).__name__}: {exc}. Read the file back before relying on either "
        "version of it, and tell the user the document may not be stored."
    )


class DocumentsBackend(ContextHubBackend):
    """A Hub documents backend that re-reads before giving up on a write.

    The documents repo has TWO writers: the agent through this backend, and a person
    saving in the browser through `web/docs.py`. So the tree moves under a turn, the
    parent commit this backend is holding stops being the head, and Hub answers the next
    push with a conflict. That would surface as a failed turn even when nothing is really
    in conflict: the person edited one document and the agent is writing another.

    Reload the tree and retry ONCE. A second conflict is a genuine race worth reporting,
    and a retry loop over someone else's commits would eventually bury one of them.

    A push can also fail in a way that says nothing about what the repo now holds: an
    HTTP response body the SDK cannot parse raises out of `push_agent` before any commit
    hash is read, so the delta may be committed, may not be, and the vendor backend only
    turns a `LangSmithError` into a result the tool layer can report. Anything else
    escapes the tool, the graph and the turn. So the three mutating entry points below
    settle that state themselves: re-read the tree, keep the work if the delta is already
    there, retry once if it is not, and report a failure the model can relay as a tool
    result naming the path. They are public API, so the recovery holds whether or not the
    private push hook `_commit` below is the route a given deepagents release takes.

    `_commit` is deepagents-internal, so the rebase depends on a private method rather
    than a public API. `test_documents.py` pins the behaviour, so a release that renames
    it fails a test here instead of quietly restoring the conflict.
    """

    def _commit(self, changes: dict[str, str | None]) -> None:
        """Push `changes`, rebasing onto the current head if someone else got there first."""
        try:
            super()._commit(changes)
        except LangSmithConflictError:
            # `changes` is a delta, so replaying it on the new head is exactly a rebase:
            # the other writer's revision survives.
            self._reread()
            super()._commit(changes)

    def _reread(self) -> dict[str, str]:
        """Drop the held snapshot and re-read the tree, refreshing the parent commit too."""
        self._cache = None
        self._commit_hash = None
        return self._ensure_cache()

    def write(self, file_path: str, content: str) -> WriteResult:
        """Commit `content` to `file_path`, settling a push the Hub never confirmed."""
        try:
            return super().write(file_path, content)
        except Exception:  # noqa: BLE001 - anything escaping the vendor backend leaves the commit state unknown
            try:
                if self._reread().get(self._strip_prefix(file_path)) == content:
                    return WriteResult(path=file_path)

                return super().write(file_path, content)
            except Exception as exc:  # noqa: BLE001 - a failed tool result, not a failed turn
                return WriteResult(error=_unconfirmed(file_path, exc))

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """Edit `file_path`, reporting rather than replaying a push the Hub never confirmed.

        Deliberately not retried: a replacement is not idempotent, so replaying one the
        repo may already hold could apply it twice. The re-read still runs, so the next
        tool call reads the tree as it actually is and the model can edit again from there.
        """
        try:
            return super().edit(file_path, old_string, new_string, replace_all)
        except Exception as exc:  # noqa: BLE001 - reported to the model rather than escaping the turn
            with contextlib.suppress(Exception):
                self._reread()

            return EditResult(error=_unconfirmed(file_path, exc))

    def delete(self, file_path: str) -> DeleteResult:
        """Delete `file_path`, settling a push the Hub never confirmed."""
        try:
            return super().delete(file_path)
        except Exception:  # noqa: BLE001 - anything escaping the vendor backend leaves the commit state unknown
            try:
                base = self._strip_prefix(file_path).rstrip("/")
                remaining = [
                    path for path in self._reread() if path == base or path.startswith(base + "/")
                ]
                if not remaining:
                    return DeleteResult(path=file_path)

                return super().delete(file_path)
            except Exception as exc:  # noqa: BLE001 - a failed tool result, not a failed turn
                return DeleteResult(error=_unconfirmed(file_path, exc))


class BackendSourceError(RuntimeError):
    """An explicitly configured Hub filesystem could not be constructed."""


def _docs_route(docs_repo: str | None, ws: str | None) -> dict[str, BackendProtocol]:
    """The artifacts mount when this assistant names a documents repo, else nothing.

    Set on BOTH topologies below rather than only the sandbox one: an assistant that
    named a documents repo asked for versioned documents, and dropping the mount
    because the assistant also happens to predate skills bundles would answer that
    request with a filesystem that silently forgets every revision.

    Never cached across runs: see the comment on the construction below.
    """
    if not docs_repo:
        return {}

    try:
        # Built fresh every resolve, deliberately NOT through `_CTXHUB_CACHE`. A cached
        # backend keeps the tree it first loaded, so after a person saves in the browser
        # the agent would read the superseded text and reason over documents that have
        # moved on, which is worse than any failure: it looks like it worked.
        return {ARTIFACTS_MOUNT: DocumentsBackend(docs_repo, client=scoped_client(ws))}
    except Exception as exc:
        raise BackendSourceError(
            f"This assistant's documents are its Context Hub repo {docs_repo!r}, mounted "
            f"at {ARTIFACTS_MOUNT}, and no backend could be built for it: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


def _resolve_backends(runtime) -> tuple[BackendProtocol, dict[str, BackendProtocol]]:
    """Resolve the current assistant's default filesystem and prefix routes.

    A sandbox default exposes execute; a StateBackend default does not. Skills
    bundles store skills at their root because CompositeBackend strips /skills/.
    Legacy assistants with only agent_repo use that whole Hub repo as their default.

    Documents routed to the Hub are reachable by the file tools but NOT by shell or
    Python in the VM, which only ever sees the VM's own disk. That boundary is why the
    mount covers the artifacts directory alone: prose and specs gain version history,
    and the data files a run computes over stay where `execute` can read them.

    Sandbox transport failures allow a state-only run. An explicitly named Hub repo
    must not silently disappear: construction failures raise BackendSourceError.
    Hub I/O happens on file access, not during backend construction.
    """
    ctx = get_ctx(runtime)
    skills_repo = ctx.skills_repo
    agent_repo = ctx.agent_repo
    ws = ctx.ls_workspace
    if agent_repo and not skills_repo:
        try:
            return _ctxhub_backend(agent_repo, ws), _docs_route(ctx.docs_repo, ws)
        except BackendSourceError:
            raise
        except Exception as exc:
            raise BackendSourceError(
                f"This assistant's whole filesystem is its Context Hub agent repo "
                f"{agent_repo!r}, and no backend could be built for it: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    vm = _get_or_create_sandbox(runtime)
    default: BackendProtocol = vm if vm is not None else StateBackend()
    routes: dict[str, BackendProtocol] = _docs_route(ctx.docs_repo, ws)
    if skills_repo:
        try:
            routes["/skills/"] = _ctxhub_backend(skills_repo, ws)
        except Exception as exc:
            raise BackendSourceError(
                f"This assistant's skills are its Context Hub repo {skills_repo!r}, mounted "
                f"at /skills/, and no backend could be built for it: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    return default, routes


class DynamicBackend(CompositeBackend):
    """A shared backend adapter that resolves the current run on every access.

    The graph serves many assistants, so default/routes must not capture one run's
    resources at construction. deepagents also reads default to decide whether to
    expose execute. Outside a run the adapter provides a StateBackend, allowing
    graph construction without acquiring infrastructure. Both shapes of "outside a
    run" count: no runtime at all, and a runtime carrying no context.
    """

    artifacts_root = "/"

    def __init__(self) -> None:
        """Leave default and routes to their per-run properties, not the base initializer."""

    def _resolve(self) -> tuple[BackendProtocol, dict[str, BackendProtocol]]:
        try:
            runtime = get_runtime()
        except Exception:  # noqa: BLE001 - graph construction has no active runtime
            return StateBackend(), {}

        # A runtime in hand does not mean a run is in flight, so this second guard is
        # load-bearing. Agent Server loads graphs through `run_in_executor`, which copies
        # the caller's contextvars into the worker thread, so `get_runtime()` can succeed
        # during graph load with no assistant behind it and no context. Resolving from
        # there reaches `seed_script_or_raise(None)`, whose refusal is right for a real
        # turn and fatal here: it leaves `build_agent`, the graph fails to load, and every
        # container exits on startup. Off a run nothing was asked for, so a plain state
        # default is the honest answer rather than a substitution.
        #
        # `getattr` because the runtime object's own shape varies, the same reason
        # `core/ctx.py:get_ctx` reads `.context` that way.
        if getattr(runtime, "context", None) is None:
            return StateBackend(), {}

        return _resolve_backends(runtime)

    @property
    def default(self) -> BackendProtocol:  # type: ignore[override]
        """This run's default backend (sandbox, Context Hub, or state)."""
        return self._resolve()[0]

    @property
    def routes(self) -> dict[str, BackendProtocol]:  # type: ignore[override]
        """This run's filesystem mounts."""
        return self._resolve()[1]

    @property
    def sorted_routes(self):  # type: ignore[override]
        """Routes ordered longest-prefix-first for CompositeBackend."""
        return sorted(self.routes.items(), key=lambda kv: len(kv[0]), reverse=True)
