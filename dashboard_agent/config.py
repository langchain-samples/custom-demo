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
      2. `dashboard-agent/.env` (this project's own file, if present).
      3. `dashboard-agent/chat-langchain-lite/.env` (sibling demo project — reuses its keys).
    """
    load_dotenv(_REPO_ROOT / ".env")
    sibling = _REPO_ROOT / "chat-langchain-lite" / ".env"
    if sibling.exists():
        load_dotenv(sibling)
    # Route LangSmith traces to the configured project (LangChain reads
    # LANGCHAIN_PROJECT; newer LangSmith also reads LANGSMITH_PROJECT).
    proj = os.getenv("PROJECT_NAME", "dashboard-agent")
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
            "ANTHROPIC_API_KEY is not set. Add it to dashboard-agent/.env or the environment."
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
            "TAVILY_API_KEY is not set. Add it to dashboard-agent/.env or the environment."
        )
    return key


MODEL = os.getenv("DASHBOARD_MODEL", "claude-sonnet-5")

# A `DASHBOARD_MODEL` carrying an explicit `provider:model` prefix picks the provider.
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
        f"{names[0]} is not set, and DASHBOARD_MODEL selects the '{provider}' provider. "
        "Add it to dashboard-agent/.env or the environment."
    )


def sampling_kwargs(default_temperature: float) -> dict[str, float]:
    """`{"temperature": ...}`, or `{}` when the model won't accept one.

    Not every model takes a temperature. Reasoning-tuned models on both Anthropic
    and Azure OpenAI reject anything but their own default and fail the call at
    invoke time, not construction — so a hardcoded `temperature=0` turns into a
    runtime 400 deep inside an eval run, which reads as "the judge is broken".

    `DASHBOARD_TEMPERATURE` unset keeps today's behavior (send the caller's value).
    Set it empty to omit temperature entirely, or to a number to force one.
    """
    load_env()
    override = os.getenv("DASHBOARD_TEMPERATURE")
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
    return os.getenv("DASHBOARD_JUDGE_MODEL", "anthropic:claude-haiku-4-5-20251001")


def goal_model() -> str:
    """Model id that grades a `/goal` against the transcript (`RubricMiddleware`).

    A separate, cheaper model than the agent's: grading runs after every turn a
    goal is set, and the judgement is a short structured verdict, not the work.
    """
    load_env()
    return os.getenv("DASHBOARD_GOAL_MODEL", "anthropic:claude-haiku-4-5-20251001")


def goal_max_iterations() -> int:
    """How many times a turn may be sent back for revision before the goal stands.

    Two, not the library's three: each retry is a whole extra agent loop, and a
    demo that silently re-runs three times reads as a hang.
    """
    load_env()
    try:
        return max(1, int(os.getenv("DASHBOARD_GOAL_MAX_ITERATIONS", "2")))
    except ValueError:
        return 2


def setup_model() -> str:
    """Model id for the assistant-setup agent (`init_chat_model` form)."""
    load_env()
    return os.getenv("DASHBOARD_SETUP_MODEL", "anthropic:claude-haiku-4-5-20251001")


def prompt_name() -> str:
    """Prompt Hub name to pull the system prompt from.

    Read after `load_env()` so a value set in `.env` is honored. The prompt is
    pulled fresh per question (see prompt.py / agent.py) so it can be edited live
    without a redeploy — this is how the planted hallucination bug is "fixed".
    """
    load_env()
    return os.getenv("DASHBOARD_PROMPT", "dashboard-agent-system")


def project_name() -> str:
    """LangSmith tracing project that agent runs are logged to."""
    load_env()
    return os.getenv("PROJECT_NAME", "dashboard-agent")


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


def dataset() -> str:
    """Which data backend the datasearch tool uses.

    "humanitarian" (default) = the bundled corpus; "synthetic" = a live LLM that
    invents plausible data per call (see datasource.py).
    """
    load_env()
    return os.getenv("DASHBOARD_DATASET", "humanitarian").strip().lower()


def data_model() -> str:
    """Model id for the synthetic data backend (a fast model; init_chat_model form)."""
    load_env()
    return os.getenv("DASHBOARD_DATA_MODEL", "anthropic:claude-haiku-4-5-20251001")


def data_prompt_name() -> str:
    """Prompt Hub name for the synthetic data-source system prompt."""
    load_env()
    return os.getenv("DASHBOARD_DATA_PROMPT", "dashboard-agent-data")


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
    return os.getenv("DASHBOARD_VOICE_MODEL", "gemini-3.1-flash-live-preview")
