"""Plant an assistant's starting files in the managed (MDA) thread sandbox.

On a LangSmith deployment the assistant owns one VM, seeded once when it is created
(`resources/sandbox.py:ensure_sandbox`). Managed Deep Agents gives every thread its own
sandbox, cloned from the `sandbox/setup.sh` snapshot, so the seed moves to the first turn
of each thread. The script is the same one, rendered by the same function, so a thread
sees exactly the files setup planned.
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langgraph.config import get_config

from custom_demo.core.ctx import get_ctx
from custom_demo.resources.sandbox import seed_script_or_raise

# Written after a successful seed. A thread's box outlives its runs, and the writer
# already refuses to overwrite, so the marker only saves re-running pip on every turn.
SEED_MARKER = "/workspace/.seeded"

# Threads seeded by this process, so a warm container skips the marker round trip too.
_seeded_threads: set[str] = set()


class SeedFailedError(RuntimeError):
    """The thread's sandbox could not be seeded with the assistant's starting files."""


def _thread_id() -> str:
    """The thread the current run belongs to, "" outside a threaded run."""
    try:
        configurable = get_config().get("configurable") or {}
    except RuntimeError:  # called outside a runnable context
        return ""

    return str(configurable.get("thread_id") or "")


class SeedThreadSandbox(AgentMiddleware):
    """Seed the thread's sandbox before the first model call that could read it.

    Inert without a sandbox: `runtime.backend` is None when the factory selected none
    (sandbox disabled, or a Hub-only assistant). With one, a missing or unusable seed
    spec raises `SeedSpecError` rather than letting the agent explore an empty
    `/workspace/data` as if that were the assistant's data, the same refusal the
    LangSmith-deployment path makes before acquiring a VM.
    """

    async def abefore_agent(self, state, runtime) -> dict[str, Any] | None:
        """Run the seed script once per thread."""
        backend = getattr(runtime, "backend", None)
        if backend is None:
            return None

        script = seed_script_or_raise(get_ctx(runtime).sandbox_seed)
        thread_id = _thread_id()
        if thread_id and thread_id in _seeded_threads:
            return None

        marker = (await backend.adownload_files([SEED_MARKER]))[0]
        if marker.error is None:
            _seeded_threads.add(thread_id)
            return None

        result = await backend.aexecute(script)
        if result.exit_code not in (0, None):
            raise SeedFailedError(
                f"Seeding /workspace/data failed with exit code {result.exit_code}: "
                f"{(result.output or '')[-500:]}"
            )

        written = await backend.awrite(SEED_MARKER, thread_id or "seeded")
        if written.error:
            raise SeedFailedError(f"Could not record the seed at {SEED_MARKER}: {written.error}")

        _seeded_threads.add(thread_id)
        return None

    def before_agent(self, state, runtime) -> dict[str, Any] | None:
        """Nothing to seed on the sync path, which never runs on MDA."""
        return None
