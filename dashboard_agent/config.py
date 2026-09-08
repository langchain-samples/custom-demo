"""Environment / configuration loading."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from langsmith import Client

_REPO_ROOT = Path(__file__).resolve().parents[1]


def load_env() -> None:
    """Load environment variables.

    Order of precedence (first wins):
      1. Variables already set in the process environment.
      2. `.env` at the repo root (this project's own file, if present).
      3. `chat-langchain-lite/.env` (sibling demo project — reuses its keys).
    """
    load_dotenv(_REPO_ROOT / ".env")
    sibling = _REPO_ROOT / "chat-langchain-lite" / ".env"
    if sibling.exists():
        load_dotenv(sibling)
    # Route LangSmith traces to the configured project (LangChain reads
    # LANGCHAIN_PROJECT; newer LangSmith also reads LANGSMITH_PROJECT).
    proj = os.getenv("PROJECT_NAME", "custom-demo")
    os.environ["LANGCHAIN_PROJECT"] = proj
    os.environ["LANGSMITH_PROJECT"] = proj


def require_anthropic_key() -> str:
    """Return the Anthropic API key, raising if it isn't configured.

    Only meaningful on the Anthropic path. `require_model_key` is the provider-aware
    gate; this stays for callers that specifically need an Anthropic credential.
    """
    load_env()
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to the repo's `.env` or the environment."
        )
    return key


def require_tavily_key() -> str:
    """Return the Tavily API key, raising if it isn't configured.

    `web_search` deliberately has no offline fallback: a missing key must surface
    as a tool error, never as invented search results.
    """
    load_env()
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        raise RuntimeError(
            "TAVILY_API_KEY is not set. Add it to the repo's `.env` or the environment."
        )
    return key


def _env(name: str, deprecated: str, default: str) -> str:
    """Read `name`, falling back to the older `deprecated` spelling, then `default`.

    The `DASHBOARD_*` names came from a time when this was only a dashboard builder.
    It reads files, runs code in a VM, writes artifacts, talks to MCP servers, drafts
    email and searches the web now, so the names dropped the prefix — but a `.env` or a
    deployment secret set under the old one has to keep working, hence the fallback.
    Empty is treated as unset, which is right for every caller here except
    `sampling_kwargs`, where empty is a meaningful value and which therefore reads the
    two names itself.
    """
    return os.getenv(name) or os.getenv(deprecated) or default


MODEL = _env("AGENT_MODEL", "DASHBOARD_MODEL", "claude-sonnet-5")

# An `AGENT_MODEL` carrying an explicit `provider:model` prefix picks the provider.
# Bare ids (the historical form, e.g. "claude-sonnet-5") stay Anthropic, so every
# existing demo keeps working untouched.
# A tuple per provider: the FIRST name is what the docs and error messages use, the rest
# are accepted aliases. Gemini has two because this repo already carries GEMINI_API_KEY
# for voice mode, and asking for the same secret twice under a second name to try the
# model would be silly.
_PROVIDER_KEYS = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "azure_openai": ("AZURE_OPENAI_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "google_genai": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    "bedrock_converse": ("AWS_ACCESS_KEY_ID",),
}


def model_provider(model_id: str | None = None) -> str:
    """Provider half of an `init_chat_model` id. Unprefixed ids are Anthropic."""
    candidate = model_id or MODEL
    provider, _, rest = candidate.partition(":")
    return provider if rest else "anthropic"


def require_model_key(model_id: str | None = None) -> str:
    """Return the API key for whichever provider `model_id` names.

    Replaces the unconditional Anthropic check at agent build time. An Azure OpenAI
    deployment needs `AZURE_OPENAI_API_KEY` and no Anthropic key at all, and hard
    failing on the latter is what stopped a non-Anthropic model from loading.
    """
    load_env()
    provider = model_provider(model_id)
    names = _PROVIDER_KEYS.get(provider)
    if names is None:
        # An unrecognized provider is not necessarily broken (it may authenticate
        # some other way), so let init_chat_model be the one to complain. This is also
        # the path a `langsmith:` id takes, where the LangSmith key authenticates and
        # there is no provider key to find.
        return ""
    for name in names:
        key = os.getenv(name)
        if key:
            # google_genai reads GOOGLE_API_KEY, so an alias has to be promoted into the
            # name the client will actually look for.
            if name != names[0]:
                os.environ.setdefault(names[0], key)
            return key
    raise RuntimeError(
        f"{names[0]} is not set, and AGENT_MODEL selects the '{provider}' provider. "
        "Add it to the repo's `.env` or the environment."
    )


def sampling_kwargs(default_temperature: float) -> dict[str, float]:
    """`{"temperature": ...}`, or `{}` when the model won't accept one.

    Not every model takes a temperature. Reasoning-tuned models on both Anthropic
    and Azure OpenAI reject anything but their own default and fail the call at
    invoke time, not construction — so a hardcoded `temperature=0` turns into a
    runtime 400 deep inside an eval run, which reads as "the judge is broken".

    `MODEL_TEMPERATURE` unset keeps today's behavior (send the caller's value).
    Set it empty to omit temperature entirely, or to a number to force one.

    Read by hand rather than through `_env`, because here the empty string is a value
    (omit temperature) and not an absence, so `or` would swallow it.
    """
    load_env()
    override = os.getenv("MODEL_TEMPERATURE")
    if override is None:
        override = os.getenv("DASHBOARD_TEMPERATURE")  # deprecated spelling
    if override is None:
        return {"temperature": default_temperature}
    if override.strip() == "":
        return {}
    return {"temperature": float(override)}


def judge_model() -> str:
    """Model id for the demo eval judge (`init_chat_model` form).

    Split from the agent model on purpose: the judge should stay pinned while the
    agent model is swapped, or an experiment comparison measures two changes at once.
    """
    load_env()
    return _env("JUDGE_MODEL", "DASHBOARD_JUDGE_MODEL", "anthropic:claude-haiku-4-5-20251001")


def goal_model() -> str:
    """Model id that grades a `/goal` against the transcript (`RubricMiddleware`).

    A separate, cheaper model than the agent's: grading runs after every turn a
    goal is set, and the judgement is a short structured verdict, not the work.
    """
    load_env()
    return _env("GOAL_MODEL", "DASHBOARD_GOAL_MODEL", "anthropic:claude-haiku-4-5-20251001")


def goal_max_iterations() -> int:
    """How many times a turn may be sent back for revision before the goal stands.

    Two, not the library's three: each retry is a whole extra agent loop, and a
    demo that silently re-runs three times reads as a hang.
    """
    load_env()
    try:
        return max(1, int(_env("GOAL_MAX_ITERATIONS", "DASHBOARD_GOAL_MAX_ITERATIONS", "2")))
    except ValueError:
        return 2


def simulated_model() -> str:
    """Fast model behind the simulated capability tools (draft_email and friends).

    Was `data_model`, back when it also drove the synthetic data source. That source
    is gone, and so is the `DASHBOARD_DATA_MODEL` fallback that survived it — carrying a
    third name for a deleted feature costs more than it protects.
    """
    load_env()
    return _env(
        "SIMULATED_MODEL", "DASHBOARD_SIMULATED_MODEL", "anthropic:claude-haiku-4-5-20251001"
    )


def setup_model() -> str:
    """Model id for the assistant-setup agent (`init_chat_model` form)."""
    load_env()
    return _env("SETUP_MODEL", "DASHBOARD_SETUP_MODEL", "anthropic:claude-haiku-4-5-20251001")


def _env_raw(name: str, deprecated: str, default: str) -> str:
    """Like `_env`, but precedence by PRESENCE rather than by truth.

    `_env` chains `or`, which is right for a model id: an empty one is not a choice,
    it is a typo, so falling through to the older spelling is the kind thing to do. The
    runtime knobs below are different. They are compared against a literal, so every
    value they can hold - the empty string included - already means something definite,
    and an explicitly empty new name has to win rather than silently hand control back
    to the deprecated one. That matters most in the test suite, where `conftest` forces
    the sandbox off by the new name while a developer's `.env` may still set the old one.
    """
    value = os.getenv(name)
    return value if value is not None else os.getenv(deprecated, default)


def sandbox_enabled() -> bool:
    """Whether the agent gets a code-execution VM. `SANDBOX_ENABLED=0` is the kill switch.

    Exactly `"0"` disables it, not any falsey-looking string. That is what the three
    call sites each compared against before this moved here, and a rename is the wrong
    change to smuggle a truthiness parser into - `SANDBOX_ENABLED=false` keeping the
    sandbox ON is surprising, but it is today's behaviour and changing it silently is
    worse than leaving it.

    Deliberately does NOT call `load_env()`. The old inline reads did not either: this
    is read at graph-build time from the deployment's real environment, and making a
    `.env` file able to speak here for the first time is not this change's business.
    """
    return _env_raw("SANDBOX_ENABLED", "DA_SANDBOX", "1") != "0"


def dynamic_subagents_enabled() -> bool:
    """Whether `task` can spin up subagents (`DYNAMIC_SUBAGENTS=1`).

    Note the polarity is the opposite of the sandbox's: off unless exactly `"1"`.
    Preserved as it was, and no `load_env()`, for the same reasons.
    """
    return _env_raw("DYNAMIC_SUBAGENTS", "DA_DYNAMIC_SUBAGENTS", "0") == "1"


def sandbox_files_root() -> str:
    """Configured root for the sandbox file browser, unvalidated.

    Resolution only. `webapp._files_root` still normalises it and rejects a relative
    root, because confining the browsable surface is the route layer's job, not the
    config layer's. `_env` (not `_env_raw`) because the old read was itself `or`-chained:
    an empty root meant `/workspace`, and it still does.
    """
    load_env()
    return _env("SANDBOX_FILES_ROOT", "DA_FILES_ROOT", "/workspace")


def mcp_tools_ttl_seconds() -> float:
    """How long a discovered MCP tool list is reused (`MCP_TOOLS_TTL`, default 120).

    `_env`'s `or` makes an empty value mean the default. The old read did not: it
    passed the empty string to `float()`, which raises at module import and takes the
    whole graph down. Fixing that is a side effect of the rename, and a strict
    improvement over a crash, but worth naming rather than leaving to be discovered.
    """
    return float(_env("MCP_TOOLS_TTL", "DA_MCP_TOOLS_TTL", "120"))


def mcp_timeout_seconds() -> float:
    """Seconds before an unreachable MCP server is given up on (`MCP_TIMEOUT`)."""
    return float(_env("MCP_TIMEOUT", "DA_MCP_TIMEOUT", "20"))


def project_name() -> str:
    """LangSmith tracing project that agent runs are logged to."""
    load_env()
    return os.getenv("PROJECT_NAME", "custom-demo")


def workspace_id() -> str | None:
    """LangSmith workspace (tenant) id to scope prompts, traces, and feedback.

    Returns None when unset, which leaves the client on the workspace tied to the
    API key (the default).
    """
    load_env()
    return os.getenv("WORKSPACE_ID") or None


def make_client():
    """Build a LangSmith Client scoped to the configured workspace (if any)."""
    return Client(workspace_id=workspace_id())


def routing_key() -> str:
    """The key used to reach ANOTHER workspace: cross-workspace first, else the default.

    The default key works for cross-workspace routing only when it is org-scoped
    (a personal access token); a workspace-scoped key cannot see its siblings.
    Returned empty rather than raising, because several callers treat "no usable
    key" as "do not route" rather than as an error.
    """
    load_env()
    return os.getenv("LS_CROSS_WORKSPACE_KEY") or os.getenv("LANGSMITH_API_KEY") or ""


def scoped_client(workspace: str | None = None) -> Client:
    """A LangSmith Client pointed at `workspace`, or at the default one.

    This shape was written out eight separate times across the package, once per
    module that needed to read another workspace, which meant eight places to fix
    when the key precedence changed. It is one function now.

    It always returns a client. Callers that must not proceed without a usable
    key check `routing_key()` first and decide for themselves what to do, because
    the three that care want three different things: skip tracing, fall back to
    the default client, or carry on and let the API refuse.
    """
    return Client(
        api_key=routing_key() or None,
        api_url=os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com"),
        workspace_id=workspace or None,
    )


def voice_model() -> str:
    """Gemini Live model the voice shell connects to (see voice.py).

    3.1 by default: it is the current low-latency audio-to-audio model, and it is what
    langchain-ai/google-adk-realtime-deepagents-example runs on.

    Its one documented limitation does NOT bite us, and it is worth knowing why before
    changing this. Google's tool-use docs mark 3.1's function calling "synchronous only"
    (`behavior: NON_BLOCKING` is 2.5-only), and a blocking call means "the model will not
    start responding until you've sent the tool response" - which for a tool that starts a
    full agent run would be tens of seconds of dead air. What avoids that is the TWO-PHASE
    response in voice.ts: acknowledge the call immediately so the model keeps the floor,
    then send the real answer as a later response. That needs no model support.

    Set this to `gemini-2.5-flash-native-audio-preview-12-2025` for the native-audio 2.5
    model (which also gets affective dialogue and proactive audio); the client adds the
    `NON_BLOCKING` flag automatically where it is supported.
    """
    load_env()
    return _env("VOICE_MODEL", "DASHBOARD_VOICE_MODEL", "gemini-3.1-flash-live-preview")
