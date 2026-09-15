"""Shared agent execution: model selection, prompts, tools and middleware ordering.

Each run uses an assistant's configuration to work with files, connected systems,
and human input. Results can be widgets, file artifacts or narrative answers.
Filesystem topology belongs to runtime/backends.py; VM lifetime belongs to the
assistant resource layer, shared with setup and file browsing.
"""

from __future__ import annotations

import contextvars
import dataclasses
from typing import Any, cast

from deepagents import RubricMiddleware, SubAgent, create_deep_agent
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    dynamic_prompt,
)
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import MemorySaver

from custom_demo.config import (
    MODEL,
    goal_max_iterations,
    goal_model,
    model_provider,
    require_model_key,
    sandbox_enabled,
)
from custom_demo.core.ctx import Context, get_ctx
from custom_demo.resources.sandbox import SEED_MAX_FILES
from custom_demo.runtime.backends import DynamicBackend
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
    """Refresh the assistant's Hub prompt and compose capabilities on each model call.

    An unset agent_repo uses FALLBACK_PROMPT; a configured repo is read afresh so
    presenter edits take effect without rebuilding the graph. Assistants with a
    Hub repo or skills bundle retain the framework's filesystem and skills guidance.
    The assistant prompt comes last. Assistants with neither use our prompt alone.
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
            for f in seeded[:SEED_MAX_FILES]
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
    """Explain the always-available JS orchestration and its separate Python data sandbox."""
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

# Every agent can dispatch these specialists through QuickJS or the task tool.
# `_subagent_specs` supplies explicit tools so no spec inherits pausing capabilities.
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


def _subagent_specs() -> list[SubAgent]:
    """Return all specialists and the general-purpose agent with explicit safe tools.

    Every spec excludes human-interrupt tools. Declaring general-purpose prevents
    framework inheritance from reintroducing those tools, and explicitly supplies
    its skill sources. Tool wrappers preserve per-invocation evaluation mocking.
    """
    tools = enable_mocking(subagent_tools())
    general_purpose: SubAgent = {
        **GENERAL_PURPOSE_SUBAGENT,
        "system_prompt": GENERAL_PURPOSE_SUBAGENT["system_prompt"] + _SUBAGENT_SOLO_CLAUSE,
        "skills": list(_SKILL_SOURCES),
    }
    return [cast("SubAgent", {**spec, "tools": tools}) for spec in (*_SUBAGENTS, general_purpose)]


class DynamicSubagentsError(RuntimeError):
    """The required QuickJS orchestration stack could not be built."""


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

    # The interpreter is required and precedes ToolSelection, which retains final filtering.
    try:
        from langchain_quickjs import CodeInterpreterMiddleware  # noqa: PLC0415 - native errors

        interpreter = CodeInterpreterMiddleware()
    except Exception as exc:
        raise DynamicSubagentsError(
            "The required QuickJS interpreter for dynamic subagents could not be built: "
            f"{type(exc).__name__}: {exc}. Check the langchain-quickjs installation and "
            "its native dependencies."
        ) from exc

    middleware = cast(
        "list[AgentMiddleware]",
        [*middleware[:-1], interpreter, middleware[-1]],
    )
    subagents = _subagent_specs()

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
