"""Assistant execution configuration supplied through the LangGraph runtime.

`Context` describes configuration, not checkpointed conversation state or VM ownership.
Middleware, tools and backend adapters share this dependency-free domain boundary.
`runtime.agent` re-exports it and registers it as the graph's `context_schema`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class Context(BaseModel):
    """Per-run configuration an assistant can set (LangGraph runtime context).

    All optional: when unset, the tools/middleware fall back to env/config, so the
    same graph works both in a deployment (assistants supply context) and locally.

    A pydantic model rather than a dataclass because LangGraph validates a BaseModel
    `context_schema` at the run boundary: a stored assistant whose `sandbox_seed` is
    not a list fails at run start, naming the field, instead of reaching a reader
    that has to re-check the shape it was already promised. Unknown keys are ignored
    (pydantic's default), which is what lets the SPA send `ls_project` for trace
    routing alongside these.
    """

    model: str | None = None  # main agent LLM (e.g. "anthropic:claude-…"); overrides build default
    agent_repo: str | None = None  # Context Hub agent repo whose AGENTS.md is the prompt
    skills_repo: str | None = (
        None  # Context Hub skills-bundle repo mounted at /skills/ (all assistants)
    )
    customer: str | None = None  # customer name - steers customer-specific synthetic data
    industry: str | None = None  # customer industry - steers synthetic data
    ls_workspace: str | None = None  # workspace to pull Hub prompts from (matches trace routing)
    # Catalogue tool ids to expose; None = defaults. The `str` arm is a TRANSPORT-SHAPE
    # DEPENDENCY: `parse_enabled` accepts "a,b" in case a transport layer stringifies the
    # field, and narrowing this to `list[str]` would turn a stored assistant carrying that
    # shape from working into a hard failure on every turn.
    enabled_tools: list[str] | str | None = None
    sandbox_seed: list[dict] | None = None  # files to plant in the VM (see render_seed_script)
    sandbox_key: str = ""  # assistant VM identity resolved by resources/sandbox.py
    # Remote MCP servers this assistant connects to: [{id?, label, url, token?, headers?}].
    # Their tools are discovered per run and namespaced `{id}_{tool}` (mcp_servers.py).
    mcp_servers: list[dict] | None = None


def get_ctx(runtime: Any) -> Context:
    """The `Context` for this run, defaults when the runtime carries none.

    Off a run (build, import, a hand-built request in a test) there is no context at
    all, and every field's default is the right answer: an assistant that named no
    `agent_repo` correctly gets `FALLBACK_PROMPT`.
    """
    context = getattr(runtime, "context", None)
    if context is None:
        return Context()

    # NORMALIZES a dict into the declared model; it does not substitute for one. A
    # malformed value still raises pydantic's `ValidationError` naming the field, so
    # nothing is silently defaulted. The branch is here because the dict shape is NOT
    # observed in production: LangGraph coerces a dict context into `Context` on every
    # invocation path we could read, but that is a reconstruction of the call chain
    # rather than an observation of a live Agent Server, and being wrong about it
    # would break every run on the deployment.
    if isinstance(context, dict):
        return Context(**context)

    return context
