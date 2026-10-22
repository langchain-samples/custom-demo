"""Context Hub mounts for the managed (MDA) runtime, applied at the tool layer.

On a LangSmith deployment `runtime/backends.py:DynamicBackend` is the agent's backend,
so `/skills/` and the documents directory are CompositeBackend routes. Managed Deep
Agents owns the backend and offers its thread sandbox or thread state only, so the same
mounts are applied one layer up: `HubRoutes` hands a file-tool call on a mounted path to
the same tool built over `HubMounts`. The skills catalogue is listed through `HubMounts`
too (`runtime/agent.py:SkillsRefresh`), so what the prompt lists is what the tools read.
"""

from __future__ import annotations

from typing import Any

from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain.agents.middleware import AgentMiddleware

from custom_demo.core.ctx import get_ctx
from custom_demo.runtime.backends import HubMounts, hub_mount_prefixes, hub_owns_filesystem

# The argument each file tool names its path with. `execute` is absent on purpose: a
# shell runs in the VM and has no way to reach a Hub repo, which is the same boundary
# DynamicBackend draws (documents are reachable by the file tools, never by Python).
_PATH_ARG = {
    "ls": "path",
    "read_file": "file_path",
    "write_file": "file_path",
    "edit_file": "file_path",
    "delete": "file_path",
    "glob": "path",
    "grep": "path",
}


def _under(path: str, prefixes: tuple[str, ...]) -> bool:
    """Whether `path` is one of the mounts or inside one (`/skills` counts as `/skills/`)."""
    return any(path == p.rstrip("/") or path.startswith(p) for p in prefixes)


class HubRoutes(AgentMiddleware):
    """Send file-tool calls on Context Hub paths to tools built over `HubMounts`.

    MDA binds its own file tools to the thread sandbox, so `read_file("/skills/x/SKILL.md")`
    would otherwise read the VM's disk and find nothing. Swapping `request.tool` is the
    same mechanism `runtime/agent.py:McpTools` uses to execute a tool the ToolNode was not
    built with, so the call keeps its id, its streaming and its HITL behaviour.

    A legacy `agent_repo`-only assistant has no sandbox at all (the factory selects
    none), and every file call goes to its Hub repo.
    """

    def __init__(self) -> None:
        """Build the Hub-bound copies of the file tools once; they resolve per run."""
        super().__init__()
        self._backend = HubMounts()
        tools = FilesystemMiddleware(backend=self._backend).tools
        self._hub_tools: dict[str, Any] = {t.name: t for t in tools if t.name in _PATH_ARG}

    def _route(self, request):
        name = request.tool_call.get("name")
        hub_tool = self._hub_tools.get(name)
        if hub_tool is None:
            return request

        ctx = get_ctx(request.runtime)
        if not hub_owns_filesystem(ctx):
            path = (request.tool_call.get("args") or {}).get(_PATH_ARG[name]) or ""
            if not _under(str(path), hub_mount_prefixes(ctx)):
                return request

        return request.override(tool=hub_tool)

    def wrap_tool_call(self, request, handler):
        """Route a Hub path on the sync path (local in-process runs and tests)."""
        return handler(self._route(request))

    async def awrap_tool_call(self, request, handler):
        """Route a Hub path on the async (deployment) path."""
        return await handler(self._route(request))
