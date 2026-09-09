"""Adapt per-run configuration to assistant resources and Context Hub filesystems.

The resource layer owns VM lifetime. This module owns only the execution topology:
a sandbox default exposes execute, and a /skills/ route exposes the Hub bundle.
"""

from __future__ import annotations

from typing import Any

from deepagents.backends import CompositeBackend, ContextHubBackend, StateBackend
from deepagents.backends.protocol import BackendProtocol
from langgraph.runtime import get_runtime

from custom_demo.config import scoped_client
from custom_demo.core.ctx import get_ctx
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


_CTXHUB_CACHE: dict[tuple[str, str | None], Any] = {}


def _ctxhub_backend(repo: str, ws: str | None) -> Any:
    """Reuse the workspace-scoped Hub backend across filesystem operations."""
    key = (repo, ws)
    backend = _CTXHUB_CACHE.get(key)
    if backend is None:
        backend = ContextHubBackend(repo, client=scoped_client(ws))
        _CTXHUB_CACHE[key] = backend

    return backend


class BackendSourceError(RuntimeError):
    """An explicitly configured Hub filesystem could not be constructed."""


def _resolve_backends(runtime) -> tuple[BackendProtocol, dict[str, BackendProtocol]]:
    """Resolve the current assistant's default filesystem and prefix routes.

    A sandbox default exposes execute; a StateBackend default does not. Skills
    bundles store skills at their root because CompositeBackend strips /skills/.
    Legacy assistants with only agent_repo use that whole Hub repo as their default.

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
            return _ctxhub_backend(agent_repo, ws), {}
        except Exception as exc:
            raise BackendSourceError(
                f"This assistant's whole filesystem is its Context Hub agent repo "
                f"{agent_repo!r}, and no backend could be built for it: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    vm = _get_or_create_sandbox(runtime)
    default: BackendProtocol = vm if vm is not None else StateBackend()
    routes: dict[str, BackendProtocol] = {}
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
    graph construction without acquiring infrastructure.
    """

    artifacts_root = "/"

    def __init__(self) -> None:
        """Leave default and routes to their per-run properties, not the base initializer."""

    def _resolve(self) -> tuple[BackendProtocol, dict[str, BackendProtocol]]:
        try:
            runtime = get_runtime()
        except Exception:  # noqa: BLE001 - graph construction has no active runtime
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
