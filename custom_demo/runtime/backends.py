"""Adapt per-run configuration to assistant resources and Context Hub filesystems.

The resource layer owns VM lifetime. This module owns only the execution topology:
a sandbox default exposes execute, a /skills/ route exposes the Hub bundle the agent
both follows and edits, and the artifacts directory routes to a Hub documents repo when
the assistant names one.
"""

from __future__ import annotations

from typing import Any

from deepagents.backends import CompositeBackend, ContextHubBackend, StateBackend
from deepagents.backends.protocol import (
    BackendProtocol,
    FileDownloadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
)
from langgraph.runtime import get_runtime

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


# A ContextHubBackend loads the repo's whole tree on first access and then holds it, and
# `ContextHubBackend._commit` folds every successful write back into that tree. So one
# instance per (repo, workspace, class) is both cheap and self-consistent for a writer
# living in this process: the agent saves a skill, and every later turn in this container
# reads it back without a Hub round trip.
#
# What a held tree cannot see is a write from somewhere else: another replica, or a
# `scripts/seed_sdlc_demo.py` re-push landing while this container is warm. Such a push
# still succeeds (ContextHubBackend reloads and replays its write on a conflict, up to
# deepagents' retry cap) but the read side stays on the tree it holds until the process
# restarts, which is the normal end of a re-push anyway.
#
# Documents cannot accept even that much, because their second writer is a person editing
# in the browser DURING a turn: see `_docs_route`.
_CTXHUB_CACHE: dict[tuple[str, str | None, str], Any] = {}


def _ctxhub_backend(repo: str, ws: str | None, cls: type[ContextHubBackend]) -> Any:
    """Reuse the workspace-scoped Hub backend of class `cls` across filesystem operations.

    `cls` is passed rather than defaulted so callers name it at the call site, where the
    module global is read. A default would bind this module's class object once at
    definition time, which is invisible until something replaces the global: the backend
    tests do exactly that, and a defaulted parameter silently ignores the replacement.
    """
    key = (repo, ws, cls.__name__)
    backend = _CTXHUB_CACHE.get(key)
    if backend is None:
        backend = cls(repo, client=scoped_client(ws))
        _CTXHUB_CACHE[key] = backend

    return backend


# Where the agent writes documents, and the mount the documents repo answers for. Shared
# with the SPA's `frontend/src/lib/artifacts.ts:ARTIFACT_DIR`, which decides the same paths
# deserve their own tab. CompositeBackend strips this prefix, so a document written to
# `/workspace/artifacts/req-204/brief.md` is stored in the repo as `req-204/brief.md`.
ARTIFACTS_MOUNT = "/workspace/artifacts/"


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
        return {ARTIFACTS_MOUNT: ContextHubBackend(docs_repo, client=scoped_client(ws))}
    except Exception as exc:
        raise BackendSourceError(
            f"This assistant's documents are its Context Hub repo {docs_repo!r}, mounted "
            f"at {ARTIFACTS_MOUNT}, and no backend could be built for it: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


def _resolve_backends(
    runtime, *, acquire_vm: bool = True
) -> tuple[BackendProtocol, dict[str, BackendProtocol]]:
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

    `acquire_vm=False` is the managed (MDA) topology, where the platform owns the
    sandbox: the default is a StateBackend placeholder and only the Hub routes are
    real (see `HubMounts`).
    """
    ctx = get_ctx(runtime)
    skills_repo = ctx.skills_repo
    agent_repo = ctx.agent_repo
    ws = ctx.ls_workspace
    if agent_repo and not skills_repo:
        try:
            return (
                _ctxhub_backend(agent_repo, ws, ContextHubBackend),
                _docs_route(ctx.docs_repo, ws),
            )
        except BackendSourceError:
            raise
        except Exception as exc:
            raise BackendSourceError(
                f"This assistant's whole filesystem is its Context Hub agent repo "
                f"{agent_repo!r}, and no backend could be built for it: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    if not acquire_vm:
        return UnmountedBackend(), _hub_routes(ctx, ws)

    vm = _get_or_create_sandbox(runtime)
    default: BackendProtocol = vm if vm is not None else StateBackend()
    routes = _hub_routes(ctx, ws)
    return default, routes


def _hub_routes(ctx: Context, ws: str | None) -> dict[str, BackendProtocol]:
    """This assistant's Context Hub mounts: the documents directory and `/skills/`."""
    routes: dict[str, BackendProtocol] = _docs_route(ctx.docs_repo, ws)
    if ctx.skills_repo:
        try:
            # Two writers: this assistant writes its own skills (see the prompt's "Your
            # skills are yours to change") and the seed script re-pushes the bundle.
            # ContextHubBackend replays a write onto a moved head itself. Cached: a held
            # tree is what makes an agent-authored skill readable on the next turn free.
            routes["/skills/"] = _ctxhub_backend(ctx.skills_repo, ws, ContextHubBackend)
        except Exception as exc:
            raise BackendSourceError(
                f"This assistant's skills are its Context Hub repo {ctx.skills_repo!r}, "
                f"mounted at /skills/, and no backend could be built for it: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    return routes


class UnmountedBackend(BackendProtocol):
    """The default under `HubMounts`: a path no Hub mount answers for holds nothing.

    Not a StateBackend. Under MDA the agent's graph state is the platform's, and a
    StateBackend reads a `files` channel that graph may not have; this placeholder is
    only ever asked to list `/skills/` for an assistant without a skills repo, which is
    empty. Writes never reach it: `HubRoutes` routes only mounted paths here.
    """

    def ls(self, path: str) -> LsResult:
        """Nothing is listed outside a mount."""
        return LsResult(entries=[])

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        """Nothing matches outside a mount."""
        return GlobResult(matches=[])

    def grep(
        self, pattern: str, path: str | None = None, glob: str | None = None, **_: Any
    ) -> GrepResult:
        """Nothing matches outside a mount."""
        return GrepResult(matches=[])

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        """No file exists outside a mount."""
        return ReadResult(error="file_not_found")

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """No file exists outside a mount."""
        return [FileDownloadResponse(path=p, error="file_not_found") for p in paths]


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

        return self._resolve_run(runtime)

    def _resolve_run(self, runtime) -> tuple[BackendProtocol, dict[str, BackendProtocol]]:
        """Resolve a run that carries assistant context."""
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


class HubMounts(DynamicBackend):
    """The Context Hub half of `DynamicBackend`, for the managed (MDA) runtime.

    MDA owns the agent's backend: its thread sandbox (or thread state) answers every
    file tool. What it cannot express is this assistant's Hub mounts, `/skills/` and the
    documents directory, so `runtime/hub_routes.py:HubRoutes` hands file calls on those
    paths to tools built over this backend instead. The default is never reached for a
    mounted path and is a StateBackend placeholder; it is the whole Hub repo for a
    legacy `agent_repo`-only assistant, which runs without a sandbox.
    """

    def _resolve_run(self, runtime) -> tuple[BackendProtocol, dict[str, BackendProtocol]]:
        """Resolve the Hub routes without acquiring an assistant VM."""
        return _resolve_backends(runtime, acquire_vm=False)


def hub_owns_filesystem(ctx: Context) -> bool:
    """Whether this assistant's whole filesystem is its Context Hub agent repo.

    The legacy topology `_resolve_backends` keeps for an assistant with `agent_repo` and
    no `skills_repo`. Shared with the MDA agent factory, which runs such an assistant
    without a sandbox so every file call reaches the Hub.
    """
    return bool(ctx.agent_repo and not ctx.skills_repo)


def hub_mount_prefixes(ctx: Context) -> tuple[str, ...]:
    """The path prefixes `HubMounts` answers for on this run, longest first."""
    prefixes = []
    if ctx.skills_repo:
        prefixes.append("/skills/")

    if ctx.docs_repo:
        prefixes.append(ARTIFACTS_MOUNT)

    return tuple(sorted(prefixes, key=len, reverse=True))
