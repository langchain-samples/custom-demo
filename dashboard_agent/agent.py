"""The Dashboard Agent deep agent.

The agent has two custom tools:

  * ``datasearch``   — dummy in-memory RAG over situation reports/assessments.
  * ``push_widget``  — appends ONE validated visualization to the live dashboard.

A run works like the CopilotKit "shared-state canvas" pattern: the agent
searches for grounded data, then emits a series of widgets that compose a
persistent dashboard, and finally writes a short narrative answer. Emitted
widgets are collected per-invocation via a ContextVar sink so the server can
return them alongside the chat text.
"""

from __future__ import annotations

import contextvars
import json
import os
import uuid
from dataclasses import dataclass
from typing import Any, cast

from deepagents import create_deep_agent
from deepagents.backends import ContextHubBackend, StateBackend
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    dynamic_prompt,
)
from langchain.chat_models import init_chat_model
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langsmith import Client

from .config import MODEL, require_anthropic_key
from .ctx import ctx_get as _ctx
from .prompt import pull_agent_prompt, pull_system_prompt
from .tools import (
    all_tools,
    allowed_tool_names,
    call_limit_middlewares,
    guidance_for,
    is_allowed,
)
from .tools import (
    widget_sink as _widget_sink,
)
from .widgets import validate_widget


@dataclass
class Context:
    """Per-run configuration an assistant can set (LangGraph runtime context).

    All optional: when unset, the tools/middleware fall back to env/config, so the
    same graph works both in a deployment (assistants supply context) and locally.
    """

    model: str | None = None  # main agent LLM (e.g. "anthropic:claude-…"); overrides build default
    prompt: str | None = None  # inline system prompt text (preferred over prompt_name)
    prompt_name: str | None = None  # system prompt in Prompt Hub
    agent_repo: str | None = None  # Context Hub agent repo whose AGENTS.md is the prompt
    dataset: str | None = None  # "humanitarian" | "synthetic"
    data_model: str | None = None  # model id for the synthetic data backend
    data_prompt_name: str | None = None  # data-source prompt in Prompt Hub
    data_prompt: str | None = (
        None  # inline data-source prompt text (preferred over data_prompt_name)
    )
    data_gap: str | None = None  # withheld topic — builds a customer-centric data prompt
    customer: str | None = None  # customer name — steers customer-specific synthetic data
    industry: str | None = None  # customer industry — steers synthetic data
    ls_workspace: str | None = None  # workspace to pull Hub prompts from (matches trace routing)
    enabled_tools: list[str] | None = None  # catalogue tool ids to expose; None = defaults


# The system prompt is sourced from LangSmith Prompt Hub (see prompt.py). We pull
# it once at the start of each question and stash it in this ContextVar, so every
# model call within one run sees a consistent prompt while a fresh question always
# re-pulls — that is what lets you fix the planted bug live in the Hub, no restart.
_prompt_override: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "prompt_override", default=None
)


@dynamic_prompt
def _hub_system_prompt(request: ModelRequest) -> str:
    """Inject the system prompt for each model call from the Hub-sourced text.

    Precedence: a per-run pulled value (set by run/run_stream) > an inline
    `prompt` from runtime context > a Context Hub `agent_repo` (its AGENTS.md) >
    the assistant's `prompt_name` from Prompt Hub > the configured default.
    """
    override = _prompt_override.get()
    if override is not None:
        base = override
    else:
        inline = _ctx(request.runtime, "prompt")
        workspace = _ctx(request.runtime, "ls_workspace")
        agent_repo = _ctx(request.runtime, "agent_repo")
        if inline:
            base = inline
        elif agent_repo:
            base = pull_agent_prompt(agent_repo, workspace=workspace)
        else:
            base = pull_system_prompt(_ctx(request.runtime, "prompt_name"), workspace=workspace)
    return base + _capability_note(request.runtime)


def _capability_note(runtime) -> str:
    """Tell the model which optional capabilities this assistant has.

    Prompts are written (and pushed to the Hub) when an assistant is created, so
    they never learn about capabilities enabled later. Appending the enabled
    catalogue tools' guidance here keeps the prompt honest without rewriting
    stored prompts.

    Returns "" for the default selection, so the common path is byte-identical to
    before (no prompt-cache churn, no behaviour change for existing assistants).
    """
    raw = _ctx(runtime, "enabled_tools")
    if raw is None:
        return ""
    allowed = allowed_tool_names(raw)
    lines = guidance_for(allowed)
    if not lines:
        return ""
    # A directive ("use only these"), not a factual claim ("these are the only
    # tools that exist"): the runtime also binds deepagents' scratch-file tools,
    # which we deliberately keep the assistant scoped away from for the demo.
    note = "\n\nAVAILABLE CAPABILITIES (use only these tools to serve the user):\n" + "\n".join(
        f"- {line}" for line in lines
    )
    # Dashboards are optional. When push_widget is off (e.g. a support/chat
    # assistant), still ground answers with datasearch but reply in prose — the
    # stored prompt's "build a dashboard" workflow does not apply.
    if "push_widget" not in allowed:
        note += (
            "\n\nDASHBOARDS ARE OFF for this assistant: do NOT build a dashboard or call "
            "push_widget (it is not available). Still call `datasearch` to ground your answer in "
            "real figures, but reply with a concise written response (a short list where helpful), "
            "not widgets."
        )
    # Stored prompts describe one rigid workflow (datasearch -> push_widget ->
    # answer). With extra capabilities enabled the model otherwise treats a
    # "draft an email" request as off-script (refusing, apologising for going
    # off-topic, or forcing a dashboard nobody asked for). Give it explicit
    # permission to answer the request that was actually made.
    if allowed - {"datasearch", "push_widget"}:
        note += (
            "\n\nThe dashboard workflow above applies to DATA questions. When the user asks "
            "for something one of the other capabilities covers, just use that capability and "
            "answer briefly — do not run a data search or build widgets first, and never say "
            "the request is off-topic."
        )
    return note


# Per-run model override. When an assistant's context sets `model`, swap the LLM
# for every model call in that run (mirrors the deepagents configurable-model
# pattern). Built models are cached by id so we don't reinit each call.
_model_cache: dict[str, Any] = {}


def _model_for(model_id: str):
    llm = _model_cache.get(model_id)
    if llm is None:
        llm = init_chat_model(model_id)
        _model_cache[model_id] = llm
    return llm


class ConfigurableModel(AgentMiddleware):
    """Override the agent LLM from `context.model` for the duration of the run.

    Implements both sync and async hooks: the local path invokes the agent
    synchronously, the Agent Server deployment runs it async.
    """

    def _apply(self, request: ModelRequest) -> ModelRequest:
        model_id = _ctx(request.runtime, "model")
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
    deepagents built-ins (`write_todos`, filesystem tools, `task`, …) alone.
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
        allowed = allowed_tool_names(_ctx(request.runtime, "enabled_tools"))
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
    key = os.getenv("LS_CROSS_WORKSPACE_KEY") or os.getenv("LANGSMITH_API_KEY")
    api_url = os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
    return Client(api_key=key, api_url=api_url, workspace_id=workspace or None)


def _backend_for(runtime):
    """Per-run filesystem backend (a deepagents BackendFactory).

    A Context Hub assistant (context.agent_repo set) mounts that agent repo, whose
    `/skills/` subtree surfaces its linked skill repos to the model; everyone else
    uses the default in-state scratch filesystem. Best-effort: any failure building
    the Hub backend falls back to StateBackend so a run never hard-fails.
    """
    repo = _ctx(runtime, "agent_repo")
    if not repo:
        return StateBackend()
    try:
        return ContextHubBackend(repo, client=_ctxhub_client(_ctx(runtime, "ls_workspace")))
    except Exception:
        return StateBackend()


def _build(model: str | None, checkpointer):
    """Shared deep-agent construction.

    `checkpointer=None` for Agent Server (it provides persistence); a MemorySaver
    for local in-process runs.
    """
    require_anthropic_key()

    # Explicit model, hardened against transient API overload (HTTP 529).
    # thinking disabled: Sonnet 5 defaults to extended thinking, whose thinking
    # blocks break the deep-agent tool loop on follow-up turns (Anthropic 400).
    llm = ChatAnthropic(
        model_name=model or MODEL,
        max_retries=8,
        timeout=120,
        max_tokens=8000,
        thinking={"type": "disabled"},
    )

    # ToolCallLimitMiddleware subclasses AgentMiddleware but binds an invariant
    # generic param that type checkers don't accept as assignable to the base — a
    # third-party typing gap, not a runtime issue. cast at this interop boundary.
    middleware = cast(
        "list[AgentMiddleware]",
        [
            ConfigurableModel(),
            _hub_system_prompt,
            # Per-run call caps declared by the registry (e.g. datasearch is capped
            # at 1/run so the agent can't wander to adjacent queries and mask the
            # planted gap). Each is inert when its tool isn't offered.
            *call_limit_middlewares(),
            # Last, so it has the final word on which tools reach the model.
            ToolSelection(),
        ],
    )

    return create_deep_agent(
        model=llm,
        # Every catalogue tool is registered; ToolSelection hides the ones this
        # assistant hasn't enabled. Built-in deepagents tools are unaffected.
        tools=all_tools(),
        middleware=middleware,
        # Context Hub assistants mount an agent repo (see _backend_for); its linked
        # skill repos live under /skills/, which this tells deepagents to surface.
        # For default (StateBackend) assistants /skills/ is empty — a no-op.
        backend=_backend_for,
        skills=["/skills/"],
        context_schema=Context,
        checkpointer=checkpointer,
    )


def build_agent(model: str | None = None):
    """Construct the deep agent for local/in-process use.

    Uses an in-memory checkpointer so a thread_id carries conversation memory.
    """
    return _build(model, MemorySaver())


def build_graph(model: str | None = None):
    """Construct the graph for Agent Server deployment.

    No checkpointer (the server injects persistence). Exposed via `graph.py` for
    langgraph.json.
    """
    return _build(model, None)


# One lazily-built agent — the prompt is dynamic, so there is no longer a
# per-variant cache (the old buggy / fixed split is gone).
_AGENT: Any = None


def get_agent():
    """Return the process-wide lazily-built local agent (built on first use)."""
    global _AGENT
    if _AGENT is None:
        _AGENT = build_agent()
    return _AGENT


def _final_text(result: dict[str, Any]) -> str:
    """Extract the assistant's final textual answer from an agent result."""
    messages = result.get("messages", [])
    for msg in reversed(messages):
        # AIMessage with no tool calls == the final answer.
        role = getattr(msg, "type", None) or (msg.get("role") if isinstance(msg, dict) else None)
        if role in ("ai", "assistant"):
            content = getattr(msg, "content", None)
            if isinstance(msg, dict):
                content = msg.get("content")
            tool_calls = getattr(msg, "tool_calls", None) or (
                msg.get("tool_calls") if isinstance(msg, dict) else None
            )
            text = _content_to_text(content)
            if text and not tool_calls:
                return text
    # Fallback: last AI text of any kind.
    for msg in reversed(messages):
        content = getattr(msg, "content", None)
        if isinstance(msg, dict):
            content = msg.get("content")
        text = _content_to_text(content)
        if text:
            return text
    return ""


def _content_to_text(content: Any, strip: bool = True) -> str:
    # strip=True for whole messages; strip=False for streaming deltas, where
    # trimming each token would delete the spaces between words.
    def _fin(s: str) -> str:
        return s.strip() if strip else s

    if content is None:
        return ""
    if isinstance(content, str):
        return _fin(content)
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return _fin("".join(parts))
    return _fin(str(content))


def run(question: str, thread_id: str = "demo", agent=None) -> dict[str, Any]:
    """Run one question through the agent.

    Returns {"answer": str, "widgets": [ ... ], "question": str}.
    Widgets are collected via a per-invocation ContextVar sink. The system prompt
    is pulled fresh from Prompt Hub for this run and pinned via a ContextVar.
    """
    agent = agent or get_agent()
    sink: list[dict] = []
    token = _widget_sink.set(sink)
    prompt_token = _prompt_override.set(pull_system_prompt())
    # Assign the trace root run id ourselves so feedback attaches to the TRACE,
    # not a child LLM/tool span.
    run_id = str(uuid.uuid4())
    try:
        result = agent.invoke(
            {"messages": [{"role": "user", "content": question}]},
            config={"run_id": run_id, "configurable": {"thread_id": thread_id}},
        )
    finally:
        _widget_sink.reset(token)
        _prompt_override.reset(prompt_token)
    return {"question": question, "answer": _final_text(result), "widgets": sink, "run_id": run_id}


def run_stream(question: str, thread_id: str = "demo", agent=None):
    """Stream a run as a sequence of event dicts, in real time.

    Yields, in the order they occur during the agent run:
      {"type": "tool", "name": ..., "summary": ...}  as each non-widget tool is called
      {"type": "widget", "widget": {...}}            as each push_widget call is made
      {"type": "answer_delta", "text": ...}          as the final answer is generated
      {"type": "answer_reset"}                        to drop a pre-tool preamble
      {"type": "run_id", "run_id": ...}              the LangSmith trace id (for feedback)
      {"type": "done"}                                when the run completes
      {"type": "error", "error": ...}                 on failure

    Widgets and tool calls are parsed from the token-level tool-call stream and
    emitted the instant each call's args finish streaming (Anthropic streams
    tool_use blocks one after another) — giving a progressive dashboard build and
    a live tool-activity feed.
    """
    agent = agent or get_agent()

    tool_bufs: dict[Any, dict[str, str]] = {}
    emitted: set = set()
    text_mids: set = set()  # message ids that streamed answer text
    reset_mids: set = set()  # message ids we've already reset (preamble)

    def _tool_summary(name: str, parsed: dict) -> str:
        if name == "datasearch":
            return str(parsed.get("query", ""))
        # Compact one-liner for any other tool (e.g. write_todos, task).
        return json.dumps(parsed, ensure_ascii=False)[:120]

    # Assign the trace root run id ourselves (via config["run_id"]) so feedback
    # attaches to the TRACE root, not a child LLM/tool span. This also avoids the
    # ContextVar issues of collect_runs() inside a streaming generator.
    root_id = str(uuid.uuid4())
    config = {"run_id": root_id, "configurable": {"thread_id": thread_id}}

    # Pull the prompt once for this question and pin it for every model call.
    prompt_token = _prompt_override.set(pull_system_prompt())
    try:
        for msg, _meta in agent.stream(
            {"messages": [{"role": "user", "content": question}]},
            config=config,
            stream_mode="messages",
        ):
            # Tool results -> a tool_result event the client attaches to the
            # matching tool chip (so it can be expanded). Skip push_widget results.
            if isinstance(msg, ToolMessage):
                tname = getattr(msg, "name", "") or ""
                if tname != "push_widget":
                    content = _content_to_text(getattr(msg, "content", None))
                    yield {
                        "type": "tool_result",
                        "id": getattr(msg, "tool_call_id", None),
                        "name": tname,
                        "content": content[:8000],
                    }
                continue

            # Otherwise only AI messages (never leak raw tool JSON into the answer).
            if not isinstance(msg, (AIMessage, AIMessageChunk)):
                continue

            mid = getattr(msg, "id", None)
            text = _content_to_text(getattr(msg, "content", None), strip=False)
            if text:
                text_mids.add(mid)
                # `mid` lets the client reset per message so pre-tool narration
                # is replaced by the final answer.
                yield {"type": "answer_delta", "text": text, "mid": mid}

            tccs = getattr(msg, "tool_call_chunks", None) or []
            # If this message narrated text and is now calling tools, it was a
            # preamble ("I'll build a dashboard…") — tell the client to drop it.
            if tccs and mid in text_mids and mid not in reset_mids:
                reset_mids.add(mid)
                yield {"type": "answer_reset"}

            for tcc in tccs:
                idx = tcc.get("index")
                if idx is None:
                    idx = tcc.get("id")
                buf = tool_bufs.setdefault(idx, {"name": "", "args": "", "id": ""})
                if tcc.get("name"):
                    buf["name"] = tcc["name"]
                if tcc.get("id"):
                    buf["id"] = tcc["id"]
                if tcc.get("args"):
                    buf["args"] += tcc["args"]

                key = buf.get("id") or idx
                if key in emitted:
                    continue
                try:
                    parsed = json.loads(buf["args"])
                except (ValueError, TypeError):
                    continue  # args not fully streamed yet
                if not isinstance(parsed, dict):
                    continue
                name = buf.get("name") or ""
                if name == "push_widget":
                    spec = parsed.get("widget", parsed)
                    if not isinstance(spec, dict):
                        continue
                    try:
                        widget = validate_widget(spec)
                    except Exception:
                        continue  # incomplete/invalid so far — wait for more
                    emitted.add(key)
                    yield {"type": "widget", "widget": widget}
                else:
                    # datasearch / built-in tools -> activity feed.
                    emitted.add(key)
                    yield {
                        "type": "tool",
                        "name": name,
                        "summary": _tool_summary(name, parsed),
                        "id": buf.get("id") or str(key),
                    }

        yield {"type": "run_id", "run_id": root_id}
        yield {"type": "done"}
    except Exception as exc:  # surface upstream/model errors to the client
        yield {"type": "error", "error": f"{type(exc).__name__}: {exc}"}
    finally:
        _prompt_override.reset(prompt_token)
