"""The demo deep agent.

Its one always-on custom tool is ``push_widget``, which appends ONE validated
visualization to the live dashboard. Everything else in the catalogue is optional
per assistant (see ``tools/registry.py``).

A run works like the CopilotKit "shared-state canvas" pattern: the agent reads
its data files, then emits a series of widgets that compose a persistent
dashboard, and finally writes a short narrative answer. Emitted
widgets are collected per-invocation via a ContextVar sink so the server can
return them alongside the chat text.
"""

from __future__ import annotations

import contextvars
import dataclasses
import json
import os
import time
from typing import Any, cast

from deepagents import RubricMiddleware, SubAgent, create_deep_agent
from deepagents.backends import (
    CompositeBackend,
    ContextHubBackend,
    LangSmithSandbox,
    StateBackend,
)
from deepagents.backends.protocol import BackendProtocol
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    dynamic_prompt,
)
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import MemorySaver
from langgraph.runtime import get_runtime
from langsmith import Client
from langsmith.sandbox import SandboxClient as _LangSmithSandboxClient

from custom_demo.config import (
    MODEL,
    dynamic_subagents_enabled,
    goal_max_iterations,
    goal_model,
    model_provider,
    require_model_key,
    sandbox_enabled,
    scoped_client,
)
from custom_demo.core.ctx import Context, get_ctx
from custom_demo.runtime.mcp_servers import instructions_for, load_tools, parse_servers
from custom_demo.runtime.mocking import enable_mocking
from custom_demo.runtime.prompt import ARTIFACT_NOTE, FALLBACK_PROMPT, pull_agent_prompt
from custom_demo.runtime.tools import (
    all_tools,
    allowed_tool_names,
    call_limit_middlewares,
    guidance_for,
    is_allowed,
    subagent_tools,
)


@dynamic_prompt
def _hub_system_prompt(request: ModelRequest) -> str:
    """Inject the system prompt for each model call, pulled fresh per question.

    Precedence for our base prompt: the assistant's Context Hub `agent_repo` (its
    AGENTS.md) > FALLBACK_PROMPT. The repo is pulled per model call rather than
    baked in at build time, which is what lets an edit to it take effect without a
    restart — fix the planted bug live in the Hub and the next question sees it.

    Context Hub assistants COMPOSE this with deepagents' middleware-built system
    prompt (`request.system_prompt`) rather than discarding it: that prompt carries
    the SkillsMiddleware catalogue, the filesystem/todo instructions, and anything
    memory injects, all of which a plain override would throw away (which is why
    skills went un-listed and the agent denied it could write files). Our prompt
    goes LAST so its persona, dashboard workflow, and failure-mode clause stay
    authoritative. This now fires for ANY assistant that has skills (`skills_repo`)
    or a Context Hub repo (`agent_repo`): skills only surface if the middleware
    catalogue reaches the model. An assistant with neither gets the clean, scoped
    prompt with no deepagents base and no filesystem instructions.
    """
    ctx = get_ctx(request.runtime)
    agent_repo = ctx.agent_repo
    if agent_repo:
        base = pull_agent_prompt(agent_repo, workspace=ctx.ls_workspace)
    else:
        base = FALLBACK_PROMPT

    ours = (
        base
        + _capability_note(request.runtime)
        + _mcp_note(request.runtime)
        + _sandbox_note(request.runtime)
        + ARTIFACT_NOTE
        + _subagents_note()
    )
    # Compose deepagents' middleware-built prompt (SkillsMiddleware catalogue,
    # filesystem/execute instructions, memory) whenever the assistant actually has
    # those capabilities — i.e. it mounts skills (`skills_repo`) or a Context Hub
    # repo (`agent_repo`). Our prompt goes LAST so its persona/workflow/failure-mode
    # clause stays authoritative. An assistant with neither runs on the clean, scoped
    # prompt alone.
    if agent_repo or ctx.skills_repo:
        framework = request.system_prompt or ""
        if framework:
            return f"{framework}\n\n{ours}"

    return ours


def _sandbox_note(runtime) -> str:
    """Tell the model it has a code-execution VM and a prepared dataset.

    deepagents already injects its own execution + host-path prompt when the
    `execute` tool is live; this is a thin, demo-specific pointer to the seeded
    data and the analyse-then-visualize workflow. Gated on the same `SANDBOX_ENABLED`
    flag as the backend so it stays off when the sandbox is disabled.
    """
    if not sandbox_enabled():
        # The agent reads files for everything, so
        # with no sandbox it has no way to look anything up. Say so plainly: left
        # unsaid, the model either invents figures or blames itself, and the
        # presenter cannot tell a misconfiguration from a bad answer.
        return (
            "\n\nNO DATA ACCESS: this assistant has no file access in this session, so you "
            "cannot look anything up. Answer from the conversation only, and say plainly "
            "that you have no data source rather than estimating a figure."
        )

    # What setup planted, named for the model. Name the files rather than giving generic
    # guidance: generic guidance sends the model to `ls` and hope, while naming them lets
    # the first turn open the right one. Still told to look, because the VM may have been
    # rebuilt or the user may have uploaded since.
    seeded = get_ctx(runtime).sandbox_seed
    listing = ""
    if seeded:
        lines = [
            f"  - /workspace/data/{f.get('name')}: {f.get('description') or f.get('kind')}"
            for f in seeded[:_SEED_MAX_FILES]
            if f.get("name")
        ]
        if lines:
            listing = "It should contain:\n" + "\n".join(lines) + "\n"

    return (
        "\n\nCODE EXECUTION: You have an isolated Linux VM with an `execute` tool. Files live in "
        "/workspace/data/, ALWAYS run `ls /workspace/data` and look at what is actually there "
        "before you plan any work, and never assume a particular file exists. "
        + (
            listing
            or "There is no file list for this assistant, so `ls` is the only way to "
            "find out what it has.\n"
        )
        + "FILES FROM THE USER: the user can upload documents and data (PDF, CSV, images) with the "
        "upload button in the Files panel, and they appear in /workspace/data. If you need a "
        "document you do not have, say exactly that and ask them to upload it there. NEVER ask "
        "them to paste a file's contents, email it, or attach it to the chat, the Files panel is "
        "the only channel, and offering another one strands the conversation.\n"
        "STILL STARTING: if /workspace/data is EMPTY, or `execute` fails to connect, the VM is "
        "almost certainly still booting - it is created when the assistant is and takes a couple "
        "of minutes to finish. Say exactly that, and offer to try again in a moment. Do NOT "
        "conclude the data is missing or misconfigured, do NOT tell the user the files are not "
        "mounted, and do NOT ask them to upload what setup already planted.\n"
        "pandas, numpy, statsmodels, scikit-learn and pypdf are already installed, so you can "
        "parse an uploaded PDF with code rather than asking the user to transcribe it. You cannot "
        "see the pixels of an image file; identify it and work with the text or data you can "
        "extract. To add libraries, `pip install --break-system-packages <packages>` (the system "
        "Python is externally managed, so a bare `pip install` will refuse). When you produce a "
        "result worth showing (a forecast, a breakdown, figures pulled out of a document), "
        "visualize it with `push_widget`, don't only describe it."
    )


def _subagents_note() -> str:
    """When dynamic subagents are on, explain orchestration vs. the data sandbox.

    Build-time gated (same env as the interpreter middleware), so it only appears
    when the JS interpreter + subagents are actually wired in.
    """
    if not _dynamic_subagents_enabled():
        return ""

    return (
        "\n\nSUBAGENTS & WORKFLOWS: For a large task with independent parts, orchestrate the "
        "specialist subagents (`researcher`, `analyst`), write a short JavaScript workflow script "
        "that fans out via the `task()` global. Use that JS interpreter ONLY for orchestration; for "
        "the actual data analysis use the Python `execute` sandbox. Don't over-orchestrate simple "
        "requests, a single tool call is usually enough."
    )


def _capability_note(runtime) -> str:
    """Tell the model which optional capabilities this assistant has.

    Prompts are written (and pushed to the Hub) when an assistant is created, so
    they never learn about capabilities enabled later. Appending the enabled
    catalogue tools' guidance here keeps the prompt honest without rewriting
    stored prompts.

    Returns "" for the default selection, so the common path is byte-identical to
    before (no prompt-cache churn, no behaviour change for existing assistants).

    Note: Context Hub assistants get their filesystem instructions from deepagents'
    base prompt (composed in by _hub_system_prompt), so no explicit file-tool note
    is needed here.
    """
    raw = get_ctx(runtime).enabled_tools
    if raw is None:
        return ""

    allowed = allowed_tool_names(raw)
    lines = guidance_for(allowed)
    if not lines:
        return ""

    # A directive ("use these"), not a factual claim ("these are the only tools
    # that exist"): the runtime also binds deepagents' scratch-file tools, which
    # an assistant with no Context Hub repo is kept scoped away from (its prompt
    # never mentions them). A Context Hub assistant gets them via the base prompt.
    note = "\n\nAVAILABLE CAPABILITIES (use these tools to serve the user):\n" + "\n".join(
        f"- {line}" for line in lines
    )
    # Dashboards are optional. When push_widget is off (e.g. a support/chat
    # assistant), still ground answers in the files but reply in prose — the
    # stored prompt's "build a dashboard" workflow does not apply.
    if "push_widget" not in allowed:
        note += (
            "\n\nDASHBOARDS ARE OFF for this assistant: do NOT build a dashboard or call "
            "push_widget (it is not available). Still read your data files to ground your answer "
            "in real figures, but reply with a concise written response (a short list where "
            "helpful), not widgets."
        )

    # Stored prompts describe one rigid workflow (read the files -> push_widget ->
    # answer). With extra capabilities enabled the model otherwise treats a
    # "draft an email" request as off-script (refusing, apologising for going
    # off-topic, or forcing a dashboard nobody asked for). Give it explicit
    # permission to answer the request that was actually made.
    if allowed - {"push_widget"}:
        note += (
            "\n\nThe dashboard workflow above applies to DATA questions. When the user asks "
            "for something one of the other capabilities covers, just use that capability and "
            "answer briefly, do not read the data files or build widgets first, and never say "
            "the request is off-topic."
        )

    return note


# The MCP tools discovered for the model call in flight. `_hub_system_prompt` is a
# sync `@dynamic_prompt`, so it cannot await discovery itself; `McpTools` runs
# first (it sits earlier in the middleware list, so it wraps further out), loads
# the tools, and leaves them here for the prompt to describe.
# None means "no discovery ran on this path", which is NOT the same as "discovery
# ran and found nothing" (an empty tuple). Only the async hook loads tools, so the
# sync path leaves None here and `_mcp_note` stays quiet on it; an empty tuple with
# servers configured is a server that could not be reached, and the model is told.
_mcp_tools: contextvars.ContextVar[tuple[Any, ...] | None] = contextvars.ContextVar(
    "mcp_tools", default=None
)


def _mcp_note(runtime) -> str:
    """Tell the model which remote MCP tools it has, and who they belong to.

    Without this the tools are still bound and still callable, but a stored prompt
    that only describes the dashboard workflow makes the model treat them as
    off-script. Naming the server is what turns `fieldlink_get_shipment` from an
    odd identifier into "the customer's own system of record".

    A configured server that yielded NO tools is reported too, rather than leaving the
    prompt silent. `load_tools` never raises (a bad server must not fail the turn),
    and the user typed that URL into the SPA, so an agent that just answers as though
    it has no connected systems is indistinguishable from a broken server. The turn
    still runs -- the model is told the connection is down so it can say so.
    """
    tools = _mcp_tools.get()
    servers = {s.id: s.label for s in _mcp_parse(get_ctx(runtime).mcp_servers)}
    if tools is None:
        return ""  # sync path: no discovery ran, so nothing is known either way

    if not tools:
        if not servers:
            return ""  # nothing configured, so nothing to report

        down = ", ".join(sorted(servers.values()))
        return (
            f"\n\nCONNECTED SYSTEMS UNAVAILABLE ({down}). This assistant is configured to "
            f"reach {down} over MCP, but it could not be reached this turn, so none of its "
            "tools are available to you. If the user asks for something only that system "
            "could answer (live order, shipment, account, ticket or inventory state), say "
            "plainly that the connection to it is down and that you cannot look it up right "
            "now, and offer to retry. Do NOT answer from your local data files as though "
            "they were that system, and do NOT invent a value it would have returned."
        )

    lines = []
    for tool in tools:
        owner = next(
            (label for sid, label in servers.items() if tool.name.startswith(f"{sid}_")), ""
        )
        # The WHOLE description, not a first line clipped to 160 characters. That
        # clip was silently cutting the back half off every remote tool, which is
        # where the cautions live: `propose_rebalance` opens with "Open the
        # rebalance app" and only later says not to propose weights. A remote
        # server's description is not ours to summarise.
        summary = (tool.description or "").strip()
        lines.append(f"- `{tool.name}`{f' ({owner})' if owner else ''}: {summary}")

    names = ", ".join(sorted(servers.values())) or "a connected MCP server"
    note = (
        f"\n\nCONNECTED SYSTEMS ({names}). These tools reach the customer's own live systems "
        "through MCP. Prefer them over your local data files for anything they cover, and never "
        "invent a "
        "value one of them could return (a tracking id, a status, a date):\n" + "\n".join(lines)
    )

    # A server's own `instructions` last, so it qualifies the tools just listed.
    # This is the only place a server can say how its tools RELATE to each other
    # ("call get_project before updating one"), which no single tool description
    # can express, and a host that drops it makes the server work around it.
    said = instructions_for(_mcp_parse(get_ctx(runtime).mcp_servers))
    for server_id, text in sorted(said.items()):
        label = servers.get(server_id, server_id)
        note += f"\n\n{label.upper()} SAYS (the server's own instructions, follow them):\n{text}"

    return note


def _mcp_parse(raw: Any):
    """`context.mcp_servers` as server records."""
    return parse_servers(raw)


class McpTools(AgentMiddleware):
    """Bind the assistant's remote MCP tools for the duration of a run.

    Which servers exist is per-assistant config, and the graph's tool list is
    fixed at build time, so MCP tools cannot be registered the ordinary way. They
    are added to `request.tools` at model-call time and handed back as concrete
    tool objects at execution time. Both halves are required:

    * `awrap_model_call` is what the model sees. Discovery is cached in
      `mcp_servers.load_tools`, so a warm call adds no latency.
    * `awrap_tool_call` is what actually runs. `ToolNode` looks a tool call up in
      the tools it was BUILT with, finds nothing for an MCP name, and passes
      `tool=None`; substituting the real tool here is what makes the call execute
      instead of erroring. Defining this hook also tells `create_agent` that this
      agent has dynamic tools, which is what stops it rejecting the unknown names
      we just added to `request.tools`.

    Async only, deliberately. MCP is an async protocol and the deployment runs the
    graph async; the sync path exists for local in-process runs and unit tests,
    where no MCP server is configured. A sync run simply sees no MCP tools.
    """

    async def _load(self, runtime) -> list[Any]:
        servers = _mcp_parse(get_ctx(runtime).mcp_servers)
        return await load_tools(servers) if servers else []

    def wrap_model_call(self, request, handler):
        """Pass through untouched on the sync path.

        MCP is an async protocol, so there is nothing to add here. This exists
        because a middleware that implements ONLY the async hook makes every
        synchronous `invoke()` raise: the evals/traffic targets and the unit tests
        both take that path, and neither has an MCP server configured.
        """
        return handler(request)

    async def awrap_model_call(self, request, handler):
        """Offer the assistant's MCP tools alongside the built-in ones."""
        tools = await self._load(request.runtime)
        token = _mcp_tools.set(tuple(tools))
        try:
            if tools:
                request = request.override(tools=[*request.tools, *tools])

            return await handler(request)
        finally:
            _mcp_tools.reset(token)

    def wrap_tool_call(self, request, handler):
        """Pass through untouched on the sync path (see `wrap_model_call`).

        Defining it also keeps `create_agent`'s dynamic-tool detection true on
        both paths, which is what stops it rejecting the names the async hook
        adds to `request.tools`.
        """
        return handler(request)

    async def awrap_tool_call(self, request, handler):
        """Execute an MCP tool call by handing `ToolNode` the tool it lacks."""
        if request.tool is None:
            name = request.tool_call.get("name")
            tool = next((t for t in await self._load(request.runtime) if t.name == name), None)
            if tool is not None:
                # `override()` does not accept `tool`, so rebuild the record.
                request = dataclasses.replace(request, tool=tool)

        return await handler(request)


def build_chat_model(model_id: str):
    """The one place a chat model is constructed. Provider-aware.

    Don't put Anthropic-specific kwargs on a hardcoded `ChatAnthropic` here: that is
    what makes the model unswappable, and a customer on an Azure OpenAI deployment can
    then set `AGENT_MODEL` and still get Claude.

    `thinking` is the reason this needs a branch rather than one kwargs dict. It is
    an Anthropic-only argument, and passing it to any other provider is a TypeError
    at construction, so the agent would fail to build rather than fail over.
    """
    provider = model_provider(model_id)
    # Bare ids are Anthropic by history; init_chat_model needs the prefix to route.
    qualified = model_id if ":" in model_id else f"anthropic:{model_id}"

    # Hardened against transient API overload (HTTP 529) on every provider.
    kwargs: dict[str, Any] = {"max_retries": 8, "timeout": 120}
    if provider == "anthropic":
        # thinking disabled: Sonnet 5 defaults to extended thinking, whose thinking
        # blocks break the deep-agent tool loop on follow-up turns (Anthropic 400).
        kwargs["max_tokens"] = 8000
        kwargs["thinking"] = {"type": "disabled"}

    return init_chat_model(qualified, **kwargs)


# Per-run model override. When an assistant's context sets `model`, swap the LLM
# for every model call in that run (mirrors the deepagents configurable-model
# pattern). Built models are cached by id so we don't reinit each call.
_model_cache: dict[str, Any] = {}


def _model_for(model_id: str):
    llm = _model_cache.get(model_id)
    if llm is None:
        # Same factory as the default model, so a per-assistant override inherits the
        # retry/timeout hardening instead of getting a bare client.
        llm = build_chat_model(model_id)
        _model_cache[model_id] = llm

    return llm


class ConfigurableModel(AgentMiddleware):
    """Override the agent LLM from `context.model` for the duration of the run.

    Implements both sync and async hooks: the local path invokes the agent
    synchronously, the Agent Server deployment runs it async.
    """

    def _apply(self, request: ModelRequest) -> ModelRequest:
        model_id = get_ctx(request.runtime).model
        return request.override(model=_model_for(model_id)) if model_id else request

    def wrap_model_call(self, request, handler):
        """Apply the context model override on the sync (local) invocation path."""
        return handler(self._apply(request))

    async def awrap_model_call(self, request, handler):
        """Apply the context model override on the async (deployment) path."""
        return await handler(self._apply(request))


class ToolSelection(AgentMiddleware):
    """Expose only the catalogue tools this assistant enabled.

    Every catalogue tool is registered on the graph at build time (deepagents'
    `tools=` is additive and cannot remove anything), so selection happens here,
    per run, by filtering `request.tools` — the same mechanism deepagents itself
    uses to drop `execute` on non-sandbox backends.

    Names outside the catalogue are never touched, which is what leaves the
    deepagents built-ins (the filesystem tools, `task`, `execute`, …) alone.
    """

    @staticmethod
    def _name(tool: Any) -> str | None:
        """Best-effort tool name from either a tool object or a dict spec."""
        if hasattr(tool, "name"):
            return tool.name

        if isinstance(tool, dict):
            return tool.get("name")

        return None

    def _apply(self, request: ModelRequest) -> ModelRequest:
        allowed = allowed_tool_names(get_ctx(request.runtime).enabled_tools)
        kept = [t for t in request.tools if is_allowed(self._name(t), allowed)]
        # Skip the override on the common path (nothing filtered).
        return request if len(kept) == len(request.tools) else request.override(tools=kept)

    def wrap_model_call(self, request, handler):
        """Filter the offered tools to the enabled set on the sync path."""
        return handler(self._apply(request))

    async def awrap_model_call(self, request, handler):
        """Filter the offered tools to the enabled set on the async path."""
        return await handler(self._apply(request))


def _ctxhub_client(workspace: str | None) -> Client:
    """LangSmith client for Context Hub reads, scoped to a workspace."""
    return scoped_client(workspace)


# --- code-execution sandbox (an isolated Linux VM behind the `execute` tool) ---
#
# The agent's default backend is a LangSmith sandbox VM: it gives the model the
# `execute` tool plus a real filesystem, so it can pip-install pandas/numpy, run
# analysis/forecasts, and write outputs. deepagents only offers `execute` when the
# CompositeBackend's *default* is a sandbox (execute is not path-routable), so the
# sandbox is the default and Context Hub `/skills/` is a route on top.
#
# The backend factory is resolved on every model call and every filesystem/execute
# tool call with no caching upstream, so we cache the VM ourselves, keyed by a
# stable per-assistant id (assistant-scoped: concurrent users of one assistant
# share a warm VM).
_SANDBOX_CACHE: dict[str, Any] = {}

# Retention, and the reason the cache above cannot be trusted forever. The service
# lifecycle is `running --(idle for idle_ttl)--> stopped --(delete_after_stop)-->
# deleted`, and NOTHING restarts a stopped VM on its own. A cached handle outlives
# both transitions — this process is long-lived — so don't trust a cached handle
# without revalidating it: a demo picked up the next day otherwise fails every
# `execute` with SandboxConnectionError instead of getting a VM back. A stopped VM
# costs no compute (only its filesystem clone is retained), so keeping it for a week
# means "tomorrow" restarts the same VM with its data intact rather than paying a ~30s
# boot and reseed.
_SANDBOX_IDLE_TTL = 3600
_SANDBOX_DELETE_AFTER_STOP = 7 * 24 * 3600

# How long a cached handle is trusted without asking the service about it. Well under
# `_SANDBOX_IDLE_TTL`, so a VM cannot stop inside a trusted window and an active
# conversation never pays for the check; a gap longer than this (someone comes back
# after lunch, or tomorrow) costs one lightweight status call.
_SANDBOX_REVALIDATE_AFTER = 600

# key -> monotonic time the cached backend was last handed out or revalidated.
_SANDBOX_SEEN: dict[str, float] = {}


# Module-level, monkeypatchable name (tests swap this for a fake). Imported at the
# top rather than behind a try: `langsmith[sandbox]` is a hard pin, the module lives
# in the base wheel, and it costs ~3ms once `deepagents.backends` is loaded. A guard
# here would only turn a broken install into an assistant with no data access.
SandboxClient: Any = _LangSmithSandboxClient

# Pre-install the data-analysis stack so the first forecast turn is instant (the system
# Python is externally managed → --break-system-packages; a bare `pip install` refuses).
# `pypdf` is in there for uploads: a presenter drops a real PDF in and the agent must be
# able to read it without a 30s install first. Best-effort `|| true` so a slow or failed
# install never blocks the data write — the agent can still install on demand.
_SEED_INSTALL = (
    "pip install --break-system-packages -q pandas numpy statsmodels scikit-learn pypdf "
    ">/dev/null 2>&1 || true\n"
)

# Caps on a per-assistant seed. The spec comes from an LLM, so it is treated as
# untrusted input for SIZE as much as for shape: a runaway `rows` would push a
# multi-megabyte heredoc through the VM's stdin.
_SEED_MAX_FILES = 4
_SEED_MAX_ROWS = 40
_SEED_MAX_TEXT = 8000
_SEED_KINDS = frozenset({"csv", "json", "txt", "md", "pdf"})


def _seed_file_name(raw: str) -> str:
    """A safe basename for a seeded file, or "" to skip it.

    The spec is model-authored, so a name is allowed to be wrong: strip it to a
    basename inside /workspace/data and drop anything that still looks hostile. Same
    rule as `_upload_name` in webapp.py, for the same reason.
    """
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
    for spec in files[:_SEED_MAX_FILES]:
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
    # The JSON goes in as a quoted heredoc ('SPEC'), so the shell performs no
    # substitution on it whatever the model wrote.
    return (
        _SEED_INSTALL
        + install
        + "mkdir -p /workspace/data\n"
        + "cat > /tmp/seed.json <<'SPEC'\n"
        + payload
        + "\nSPEC\n"
        + _SEED_WRITER
    )


# Writes /tmp/seed.json into /workspace/data. Pure stdlib apart from the optional
# fpdf2 import, and every failure is per-file: one bad spec entry must not cost the
# others.
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


# (source, key) pairs already reported by `_sandbox_key_from`, so a fallback is
# logged once per process rather than once per filesystem call.
_KEY_SOURCE_REPORTED: set[tuple[str, str]] = set()


def _sandbox_key_from(
    sandbox_key: str | None, agent_repo: str | None = None, customer: str | None = None
) -> str:
    """The cache/VM key for an assistant: its own key, else agent_repo, else customer.

    `sandbox_key` is minted per assistant at setup (`prepare_assistant`) and is the
    only one of the three that is unique. Don't let a new assistant fall back to the
    other two if you can avoid it: they are derived from the customer name, so a
    second assistant for the same customer resolves to the SAME VM and attaches to it
    instead of creating one, inheriting that VM's files and skipping its own seed.
    That is how a McKesson assistant ended up holding nothing but the `sales.csv` a
    previous McKesson assistant's failed setup had planted, in production.

    They remain as fallbacks because every assistant provisioned before `sandbox_key`
    carries no key, and sharing a warm VM is still better for them than having none.
    They are AUDIBLE, though: "which VM did this assistant attach to, and why" has to
    be answerable from stdout, because the chain is what produced that bug and a
    shared VM looks exactly like an assistant's own until you read its files.
    """
    if sandbox_key:
        return sandbox_key

    if agent_repo:
        source, key = "agent_repo", agent_repo
    elif customer:
        source, key = "customer name", customer
    else:
        source, key = "neither, so the process-wide shared", "default"

    # Once per distinct fallback, not once per resolve: `_resolve_backends` re-runs on
    # every filesystem property access, so an unconditional print would bury the log.
    if (source, key) not in _KEY_SOURCE_REPORTED:
        _KEY_SOURCE_REPORTED.add((source, key))
        print(
            f"[sandbox] this assistant has no sandbox_key of its own, so it attaches to "
            f"the VM named for its {source} ({key!r}). Any other assistant resolving to "
            f"the same name SHARES that VM and its files, and skips its own seed."
        )

    return key


def _sandbox_key_credentials() -> tuple[str | None, dict[str, str]]:
    """`(api_key, headers)` for a `SandboxClient`.

    An ORG-scoped LangSmith key has no default workspace, and `SandboxClient` sends
    only `X-Api-Key` — it reads no workspace env var and takes no workspace argument.
    So every sandbox call 403s on such a key, `_ensure_sandbox` swallows that as "no
    VM", and the run degrades to StateBackend with an empty `/workspace/data` and no
    error anywhere. Sending the tenant explicitly is what gets the control-plane calls
    (list/get/create) working.

    It is not sufficient on its own: the data plane (`execute`, and so every file
    listing) additionally rejects an org-scoped key with "insufficient sandbox
    access". A deployment is unaffected — the platform injects its own
    workspace-scoped `LANGSMITH_API_KEY` — but a LOCAL run against an org key needs a
    workspace-scoped key in `LANGSMITH_API_KEY` to actually reach a VM.
    """
    api_key = os.getenv("LANGSMITH_API_KEY") or os.getenv("LS_CROSS_WORKSPACE_KEY") or None
    workspace = os.getenv("LANGSMITH_WORKSPACE_ID") or os.getenv("WORKSPACE_ID") or ""
    return api_key, {"X-Tenant-Id": workspace} if workspace else {}


def _sandbox_enabled() -> bool:
    """Whether a sandbox can be built at all (extra present, flag on, creds set)."""
    if SandboxClient is None or not sandbox_enabled():
        return False

    return bool(_sandbox_key_credentials()[0])


class SeedSpecError(RuntimeError):
    """An assistant's VM was created with no usable starting-files spec.

    Typed so callers and tests can recognize it without reading its message (the
    message is for the human in front of the SPA, and is free to change).

    There is deliberately no stand-in dataset behind this. Don't reuse one seed
    script across all assistant types — an assistant scoped to one domain can end up
    loaded with an unrelated dataset (e.g. medical-records loaded with retail revenue
    data), which has happened in production, and the agent then reasons over those
    figures as confidently as over its own.
    """


def seed_script_or_raise(seed: list[dict] | None) -> str:
    """The shell script that plants `seed`, or `SeedSpecError` naming what was missing.

    Shared by the two callers that must agree: `_get_or_create_sandbox` checks it
    BEFORE a VM is acquired, so an assistant with no usable spec never gets one, and
    `_seed_data` renders it on the create path. Checking up front is what makes the
    failure identical on every turn. Don't check only at seed time: turn 1 then fails
    loudly, creates an empty VM anyway, and turn 2 attaches to it and answers from an
    empty /workspace/data -- which reads as a recovery, and is the shape this whole
    path exists to avoid.
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
    """Plant this assistant's starting files inside a freshly created VM.

    `seed` is the assistant's own spec (`Context.sandbox_seed`). Raises
    `SeedSpecError`, naming what was missing, when there is nothing usable to plant:
    an assistant whose stored context carries no spec, or one whose spec is entirely
    unusable, gets a visibly failed turn rather than somebody else's dataset (see
    `SeedSpecError`). `_ensure_sandbox` lets that one through on purpose.

    The VM round trip itself stays best-effort: the spec was fine, the platform was
    not, and that is a retry rather than a wrong answer.

    Called only on the create path. It is a blocking VM round trip that begins with a
    pip install, so it must never sit on a turn that merely attached to a warm VM.
    """
    script = seed_script_or_raise(seed)
    try:
        backend.execute(script)
    except Exception as exc:  # noqa: BLE001 - a VM/transport failure must not fail the run
        # Printed, not swallowed: an empty /workspace/data is reported by the model as
        # its data not being mounted, which reads as a platform fault rather than this.
        print(f"[sandbox] seeding failed: {type(exc).__name__}: {exc}")


# How long a turn will wait for a VM to finish booting before giving up on it. Setup
# pre-warms at provisioning, but provisioning finishes in ~40s and a boot plus the
# pandas/numpy/statsmodels/scikit-learn install takes longer than that - so the first
# question can genuinely arrive before the VM is usable. Waiting makes that turn slow;
# not waiting makes it look like the assistant had no data at all.
_SANDBOX_WAIT_SECONDS = 25.0
_SANDBOX_POLL_SECONDS = 1.5


def _status_or_none(client: Any, name: str) -> str | None:
    """The VM's status, or None when the service cannot tell us.

    The two are NOT the same and conflating them is a regression waiting to happen: an SDK
    without `get_sandbox_status`, or a call that errors, means "unknown", and treating
    unknown as "not ready" would refuse a perfectly good VM after a pointless wait.
    """
    try:
        status = client.get_sandbox_status(name)
    except Exception:  # noqa: BLE001 - gone, unreachable, or an SDK without the call
        return None

    value = str(getattr(status, "status", "") or "").lower()
    return value or None


def _wait_ready(client: Any, name: str, seconds: float = _SANDBOX_WAIT_SECONDS) -> bool:
    """Poll until the VM reports ready, or `seconds` elapse. True if it got there.

    A booting VM is NOT "stopped", so `_acquire_raw` hands it straight back: without
    this wait the first read against it fails, which the model then reports as its files
    not being mounted.

    An UNKNOWABLE status returns True immediately rather than waiting, because blocking a
    turn for 25s on an SDK that simply has no status endpoint would be a worse bug than
    the missed boot this guards against.
    """
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

        # Nothing auto-starts a stopped VM, so don't skip this: the 1-2h stopped
        # window otherwise looks identical to a healthy VM right up until the first
        # command fails to connect.
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


def _ensure_sandbox(key: str, *, create: bool = True, seed: list[dict] | None = None) -> Any | None:
    """Get/revalidate/reattach/create the sandbox VM named for `key`, seeding once.

    Reuses the process cache while it is fresh, then re-checks it against the service
    (a cached handle outlives the VM — see `_SANDBOX_REVALIDATE_AFTER`), then attaches
    to a VM surviving a restart / made by another replica or the pre-warm step
    (restarting it if stopped), else creates + seeds one. Any failure returns None so
    callers degrade to StateBackend.

    `create=False` is attach-only: read-only callers (the `/sandbox-files` file
    browser in webapp.py) must never provision infrastructure — creating costs a
    ~30s boot plus a pip install, which is not something a UI click may trigger.

    `seed` is this assistant's starting files; it only matters on the call that creates
    the VM, and an attach reuses whatever the filesystem already holds.

    `SeedSpecError` is the one failure that is NOT degraded to None. Every other
    sandbox problem is the platform having a bad minute, which a retry fixes; a
    missing seed spec is a misconfigured assistant, and answering it from a
    StateBackend (or from a dataset that belongs to some other customer) is the
    silent-wrong-answer this whole path exists to avoid.
    """
    cached = _SANDBOX_CACHE.get(key)
    now = time.monotonic()
    if cached is not None and now - _SANDBOX_SEEN.get(key, 0.0) < _SANDBOX_REVALIDATE_AFTER:
        return cached

    try:
        return _revalidate_or_acquire(key, cached, now, create=create, seed=seed)
    except SeedSpecError:
        raise
    except Exception:  # noqa: BLE001 - never hard-fail a run on sandbox trouble
        return None


def _revalidate_or_acquire(
    key: str, cached: Any, now: float, *, create: bool, seed: list[dict] | None
) -> Any | None:
    """The cache-miss half of `_ensure_sandbox`: recheck `cached`, else attach/create.

    Split out so the sequence reads as a flat series of early returns rather than one
    long block inside a try. Raises freely — `_ensure_sandbox` owns the catch-all that
    degrades a sandbox failure to StateBackend.
    """
    api_key, headers = _sandbox_key_credentials()
    client = SandboxClient(api_key=api_key, headers=headers or None)
    name = f"da-{_slug(key)}"
    # A deleted VM answers 404 and the SDK raises, which says the same thing as
    # an explicit non-ready status: this caller needs a different VM. The status
    # endpoint is used rather than list_sandboxes because this is the hot path.
    if cached is not None and _status_or_none(client, name) == "ready":
        _SANDBOX_SEEN[key] = now
        return cached

    # Past here the cached handle (if any) is dead: drop it rather than hand it
    # back, so a caller that cannot get a VM degrades to StateBackend instead of
    # calling a VM that no longer exists.
    _SANDBOX_CACHE.pop(key, None)
    _SANDBOX_SEEN.pop(key, None)
    got = _acquire_raw(client, name, create=create)
    if got is None:
        return None

    raw, created = got
    # Wait for it to actually be up. Before seeding, not after: `_seed_data` swallows
    # its own failures, so seeding a VM that has not finished booting produces an
    # empty /workspace/data and no error anywhere.
    if not _wait_ready(client, name):
        return None

    backend = LangSmithSandbox(raw)
    # ONLY on create. Don't seed on the attach path to repair a VM built for a
    # different assistant: the seed script opens with a pip install of
    # pandas/numpy/statsmodels/scikit-learn, and this runs inside the first middleware
    # that touches the filesystem, with no timeout, so every turn that misses the cache
    # hangs indefinitely. An assistant that predates `sandbox_key` and holds another
    # assistant's files has to be recreated.
    if created:
        _seed_data(backend, seed)

    _SANDBOX_CACHE[key] = backend
    _SANDBOX_SEEN[key] = now
    return backend


def _get_or_create_sandbox(runtime) -> Any | None:
    """The warm sandbox for this run's assistant, or None if unavailable.

    Passes the assistant's seed spec so a VM created lazily on the first turn gets the
    same files the setup prewarm would have planted.
    """
    if not _sandbox_enabled():
        return None

    ctx = get_ctx(runtime)
    spec = ctx.sandbox_seed
    # Before the VM, not after it. See `seed_script_or_raise`: an assistant with no
    # usable spec must fail the same way on every turn, rather than failing once and
    # then quietly attaching to the empty VM that first turn left behind.
    seed_script_or_raise(spec)
    key = _sandbox_key_from(ctx.sandbox_key, ctx.agent_repo, ctx.customer)
    return _ensure_sandbox(key, seed=spec)


def prewarm_sandbox(
    agent_repo: str | None = None,
    customer: str | None = None,
    seed: list[dict] | None = None,
    sandbox_key: str | None = None,
) -> None:
    """Create + seed an assistant's VM ahead of its first chat (fire-and-forget).

    Called from assistant setup so the ~30s VM boot + data seed happens in the
    background at provisioning instead of blocking the user's first message. Keyed
    exactly like the runtime (agent_repo → customer), so the first turn reattaches
    the same warm VM by name rather than creating a second one. Best-effort: a
    no-op when the sandbox is disabled/unavailable, and never raises.

    Printed, not swallowed. This runs on a daemon thread with nobody to raise to, so
    the log line is the only trace: without it a `SeedSpecError` here (an assistant
    whose seed spec never made it into its context) looked exactly like a successful
    pre-warm right up until the first question.
    """
    if not _sandbox_enabled():
        return

    try:
        _ensure_sandbox(_sandbox_key_from(sandbox_key, agent_repo, customer), seed=seed)
    except Exception as exc:  # noqa: BLE001 - provisioning must never fail on a warm-up
        print(f"[sandbox] prewarm failed: {type(exc).__name__}: {exc}")


# Context Hub backends are keyed by (repo, workspace) so a run's repeated filesystem
# calls reuse one instance instead of rebuilding a LangSmith client each call: the
# backend factory is resolved on every filesystem access, with no caching upstream.
_CTXHUB_CACHE: dict[tuple[str, str | None], Any] = {}


def _ctxhub_backend(repo: str, ws: str | None) -> Any:
    """Get/create a cached ContextHubBackend for a repo (raises on construction error)."""
    key = (repo, ws)
    backend = _CTXHUB_CACHE.get(key)
    if backend is None:
        backend = ContextHubBackend(repo, client=_ctxhub_client(ws))
        _CTXHUB_CACHE[key] = backend

    return backend


class BackendSourceError(RuntimeError):
    """A configured Context Hub repo could not be turned into a filesystem backend.

    Typed so callers and tests can recognize it without reading its message (the
    message is for the human in front of the SPA, and is free to change).

    Same stance as `PromptSourceError`: an assistant whose context NAMES a repo asked
    for that repo. Resolving it to a `StateBackend` (or to no `/skills/` route) leaves
    a run whose prompt tells the model to consult its skills, and whose skills are not
    there, which is indistinguishable from a customer who has none.
    """


def _resolve_backends(runtime) -> tuple[BackendProtocol, dict[str, BackendProtocol]]:
    """Resolve this run's (default, routes) filesystem backends from runtime context.

    The sandbox VM is the **default** when available (so `execute` is offered —
    it's not path-routable, deepagents keys it off the default), and the assistant's
    Context Hub skills-bundle repo mounts at `/skills/` (live, read/write). The bundle
    stores each skill at its ROOT (`<name>/SKILL.md`) so the plain route works — the
    composite strips the `/skills/` prefix, and the repo's keys have no `skills/`
    prefix to lose. Every assistant gets skills this way, independent of whether its
    prompt lives in Context Hub.

    Degrades gracefully:
    - sandbox unavailable (`SANDBOX_ENABLED=0`, no entitlement, no network) → StateBackend
      default (no `execute`); skills still mount if present.
    - no skills + no sandbox → plain StateBackend (exactly today's default).
    - Back-compat: an old Context Hub assistant has `agent_repo` but no `skills_repo`
      — its skills live under the agent repo's `/skills/` subtree, which can't be
      prefix-routed, so mount the whole repo as the default (no execute) rather than
      regress its skills. New assistants set `skills_repo` and get both.

    What it does NOT degrade is a repo the assistant's context actually names: that
    raises `BackendSourceError`. Note what is and is not in scope there —
    `ContextHubBackend.__init__` performs no I/O (it stores the identifier and an
    already-built client), so a Hub OUTAGE does not surface here at all; it surfaces
    at the first `ls`/`read_file` through the backend. Only a client that cannot be
    constructed reaches these handlers, which is a misconfiguration, not a bad minute.
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

    sandbox = _get_or_create_sandbox(runtime)
    default: BackendProtocol = sandbox if sandbox is not None else StateBackend()
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
    """A CompositeBackend whose default + routes are chosen per run, from context.

    deepagents 0.7 removed the callable backend factory (`backend=_backend_for`), so
    per-run backend selection — sandbox VM vs Context Hub vs plain state, keyed on the
    assistant's runtime context — moves *inside* one concrete backend. Each filesystem/
    `execute` call resolves the underlying backends via `get_runtime()` and lets
    `CompositeBackend`'s routing do the rest. `default`/`routes`/`sorted_routes` are
    live properties (not `__init__` attributes), so a single shared instance serves
    every assistant. Because deepagents keys `supports_execution()` off `.default`,
    the `execute` tool is offered iff THIS run resolved a sandbox VM — matching the
    pre-0.7 per-run behavior. Off a run (build/import/tests) there is either no runtime
    or a runtime carrying no context, and both fall back to a plain `StateBackend`, so
    construction and graph load never fail.
    """

    artifacts_root = "/"

    def __init__(self) -> None:  # noqa: D107 - see class docstring
        # Intentionally does NOT call super().__init__: default/routes/sorted_routes
        # are resolved per run by the properties below, not stored at construction.
        pass

    def _resolve(self) -> tuple[BackendProtocol, dict[str, BackendProtocol]]:
        try:
            runtime = get_runtime()
        except Exception:  # noqa: BLE001 - off a run (build/tests): safe default
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
        """This run's default backend (sandbox VM, Context Hub, or state)."""
        return self._resolve()[0]

    @property
    def routes(self) -> dict[str, BackendProtocol]:  # type: ignore[override]
        """This run's prefix routes (e.g. ``/skills/`` → Context Hub)."""
        return self._resolve()[1]

    @property
    def sorted_routes(self):  # type: ignore[override]
        """Routes sorted longest-prefix-first, as CompositeBackend expects."""
        return sorted(self.routes.items(), key=lambda kv: len(kv[0]), reverse=True)


# Where deepagents looks for SKILL.md bundles. One prefix, resolved per run by
# DynamicBackend to the assistant's Context Hub skills repo (empty for a StateBackend
# assistant, so a no-op). Named once because the main agent and the general-purpose
# subagent both declare it and they have to agree.
_SKILL_SOURCES: tuple[str, ...] = ("/skills/",)

# Appended to every subagent's prompt. The tools are already withheld (see
# `_subagent_specs`), so this is not the enforcement: it is what stops the model
# SPENDING a step reaching for a pause it cannot have, and names the alternative.
# A subagent that treats its reply as a status update rather than the deliverable
# is the failure this closes: its caller has nothing to synthesize from.
_SUBAGENT_SOLO_CLAUSE = (
    " You are working alone: nobody reads your output but the agent that called you, and "
    "you cannot pause for a human, ask a question or hand anything to the user. Never "
    "narrate what you are about to do next. Your reply IS the deliverable, so return the "
    "finished result, and if you cannot get it, say what stopped you and what you did get."
)

# Dynamic subagents (deepagents + a QuickJS code-interpreter, langchain-quickjs):
# the agent writes a JS orchestration script that fans work out to these subagents
# via a `task()` global. A small fixed generalist set — the value is the
# orchestration, not per-domain specialization. Gated behind DYNAMIC_SUBAGENTS
# (build-time env; off by default) because the interpreter middleware is fixed at
# build and we want a deploy-safe default we can flip on after verification.
#
# `tools` is deliberately absent from these literals: `_subagent_specs` stamps it on
# every spec, so a new entry here cannot reintroduce the inherit-everything default.
_SUBAGENTS: list[SubAgent] = [
    {
        "name": "researcher",
        "description": "Researches ONE focused question and returns concise, grounded findings.",
        "system_prompt": (
            "You are a focused researcher. Look up the requested information and return concise, "
            "grounded findings with the concrete figures. Do not build dashboards."
            + _SUBAGENT_SOLO_CLAUSE
        ),
    },
    {
        "name": "analyst",
        "description": "Runs ONE focused data-analysis task and returns the computed result.",
        "system_prompt": (
            "You are a data analyst. Compute the requested result (use the `execute` tool for "
            "Python, pandas/numpy/statsmodels are available) and return it succinctly."
            + _SUBAGENT_SOLO_CLAUSE
        ),
    },
]


def _subagent_specs(*, dynamic: bool) -> list[SubAgent]:
    """Every subagent the main agent can dispatch to, each pinned to safe tools.

    Three things are settled here rather than in the literals above.

    `tools` is stamped on EVERY spec. A `SubAgent` that omits the key inherits the
    main agent's entire tool list, which is how `ask_user` reached a subagent and hung
    the graph on a question no client could answer; `subagent_tools()` is that list
    minus the rows that pause for a human. Stamping it centrally, rather than writing
    the key into each literal, is what makes the guarantee hold for the next subagent
    somebody adds.

    `general-purpose` is listed WHATEVER `dynamic` says, and that is the load-bearing
    part. deepagents appends its own copy whenever the caller names none, built from
    the main agent's tools and carrying the same fault, and `FilesystemMiddleware`
    offers `task` either way: a deployment with the flag off therefore still has a
    dispatchable subagent holding `ask_user`. Naming it here replaces that copy on
    both paths. Its identity comes from the framework's own spec so it keeps the
    description and prompt the framework advertises, and `skills` is re-declared
    because an inline spec only mounts the sources it asks for.

    The `researcher`/`analyst` pair stays gated: they exist to be fanned out to by the
    QuickJS orchestration script, so advertising them without the interpreter would
    offer the model dispatch targets its prompt never explains.

    Args:
        dynamic: Whether the QuickJS orchestration stack is wired in
            (`DYNAMIC_SUBAGENTS`).

    Returns:
        Specs for `create_deep_agent`, in dispatch-menu order.
    """
    # Mock-wrapped like the main agent's, so a dataset row's `mock_tools` reaches a
    # tool call made inside a subagent too. Inert with no spec installed.
    tools = enable_mocking(subagent_tools())
    general_purpose: SubAgent = {
        **GENERAL_PURPOSE_SUBAGENT,
        "system_prompt": GENERAL_PURPOSE_SUBAGENT["system_prompt"] + _SUBAGENT_SOLO_CLAUSE,
        "skills": list(_SKILL_SOURCES),
    }
    specs = (*_SUBAGENTS, general_purpose) if dynamic else (general_purpose,)
    return [cast("SubAgent", {**spec, "tools": tools}) for spec in specs]


class DynamicSubagentsError(RuntimeError):
    """`DYNAMIC_SUBAGENTS` is on but the dynamic-subagent stack could not be built.

    Typed so callers and tests can recognize it without reading its message (the
    message is for the operator who set the flag, and is free to change).
    """


def _dynamic_subagents_enabled() -> bool:
    return dynamic_subagents_enabled()


def _rubric_middleware() -> RubricMiddleware:
    """The `RubricMiddleware` that drives the SPA's goal pill.

    The client puts the user's `/goal` on the run as `rubric`, and after each turn a
    grader model checks the transcript against it, sending the agent back for another
    pass until it is satisfied (capped). Both of its hooks no-op when no rubric is on
    the state, so an ordinary turn is untouched — which is also why this is always on
    rather than env-gated.

    NOT guarded, and not optional. `RubricMiddleware` is imported at the top of this
    module from `deepagents`, which is pinned `>=0.7,<0.8` in `[project]
    dependencies`, so a deepagents without it is a state the resolver cannot produce.
    Don't guard it: a guard for that impossible case also swallows any OTHER failure
    (a bad `GOAL_MODEL`, say) and returns None, building a graph whose goal pill accepts
    a goal and then never grades it, with nothing anywhere saying why. Let it raise: an
    unbuildable grader is a broken deployment.
    """
    return RubricMiddleware(model=goal_model(), max_iterations=goal_max_iterations())


def _build_agent(model: str | None, checkpointer):
    """Construct the deep agent. See `build_agent` for the public entry point."""
    model_id = model or MODEL
    require_model_key(model_id)
    llm = build_chat_model(model_id)

    # ToolCallLimitMiddleware subclasses AgentMiddleware but binds an invariant
    # generic param that type checkers don't accept as assignable to the base — a
    # third-party typing gap, not a runtime issue. cast at this interop boundary.
    middleware = cast(
        "list[AgentMiddleware]",
        [
            ConfigurableModel(),
            # Before the prompt middleware, so the tools it discovers are already
            # in the ContextVar when `_mcp_note` describes them to the model.
            McpTools(),
            _hub_system_prompt,
            # Per-run call caps declared by the registry. Each is inert when its
            # tool isn't offered.
            *call_limit_middlewares(),
            # Last, so it has the final word on which tools reach the model.
            ToolSelection(),
        ],
    )

    # Goal grading (`/goal` in the SPA). First in the list so its `after_agent`
    # runs LAST — after hooks fire in reverse order, and this one is the gate that
    # decides whether the turn is finished at all. Inert without a `rubric`.
    middleware = [_rubric_middleware(), *middleware]

    # Dynamic subagents (opt-in): add the QuickJS interpreter middleware BEFORE
    # ToolSelection, which must stay LAST so it keeps the final word on which tools
    # reach the model. The import is function-local for COST, not safety:
    # `langchain_quickjs` pulls a native quickjs-rs and costs ~430ms to import, which
    # a deployment with DYNAMIC_SUBAGENTS off must not pay at every cold start (same
    # reason as the fastmcp imports in mcp_servers.py).
    #
    # `subagents` is passed either way, never None: deepagents fills a None in with a
    # general-purpose subagent holding every main-agent tool, and `task` is offered
    # whatever this flag says, so leaving it None is what would put `ask_user` back
    # inside a subagent on the default configuration.
    dynamic = _dynamic_subagents_enabled()
    if dynamic:
        try:
            from langchain_quickjs import CodeInterpreterMiddleware  # noqa: PLC0415

            interpreter = CodeInterpreterMiddleware()
        except Exception as exc:
            # An OPT-IN capability, so silence is not an option: somebody set
            # DYNAMIC_SUBAGENTS and would otherwise get an agent with no subagents,
            # no error, and skills whose workflows tell it to fan work out to them.
            # `langchain-quickjs` is a hard pin (>=0.3,<0.4), so this is a broken
            # install or a bad build, and the operator who set the flag is the one
            # who can act on it.
            raise DynamicSubagentsError(
                "DYNAMIC_SUBAGENTS is on, but the QuickJS code interpreter behind the "
                f"dynamic subagents could not be built: {type(exc).__name__}: {exc}. "
                "Unset DYNAMIC_SUBAGENTS to run without them."
            ) from exc

        middleware = cast(
            "list[AgentMiddleware]",
            [*middleware[:-1], interpreter, middleware[-1]],
        )

    subagents = _subagent_specs(dynamic=dynamic)

    return create_deep_agent(
        model=llm,
        # Every catalogue tool is registered; ToolSelection hides the ones this
        # assistant hasn't enabled. Built-in deepagents tools are unaffected.
        # Wrapped so a dataset row can mock any tool per invocation. Inert unless
        # `mocking.using_mocks` installed a spec, so the deployment is unaffected.
        tools=enable_mocking(all_tools()),
        # cast: our middleware list is typed with ContextT=None, but create_deep_agent
        # binds ContextT to our context_schema (Context). Assignable at runtime; the
        # variance mismatch is a typing-only interop gap.
        middleware=cast("Any", middleware),
        subagents=subagents,
        # One shared backend that resolves per run (see DynamicBackend): sandbox VM
        # default + the assistant's Context Hub skills repo mounted at /skills/, which
        # this tells deepagents to surface. For default (StateBackend) assistants
        # /skills/ is empty — a no-op.
        backend=DynamicBackend(),
        skills=list(_SKILL_SOURCES),
        context_schema=Context,
        checkpointer=checkpointer,
    )


def build_agent(model: str | None = None, *, deployed: bool = False):
    """The deep agent, for a local run or for Agent Server.

    The only difference between the two is where conversation state lives, so it
    is one argument rather than a second entry point: a local run gets an
    in-memory checkpointer so a `thread_id` carries memory, and a deployment
    passes none because Agent Server injects persistence itself.
    """
    return _build_agent(model, None if deployed else MemorySaver())


# One lazily-built agent: the prompt is dynamic, so one instance serves every variant
# and there is no per-variant cache.
_AGENT: Any = None


def get_agent():
    """Return the process-wide lazily-built local agent (built on first use)."""
    global _AGENT
    if _AGENT is None:
        _AGENT = build_agent()

    return _AGENT
