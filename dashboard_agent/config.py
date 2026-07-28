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
    load_env()
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to dashboard-agent/.env or the environment."
        )
    return key


MODEL = os.getenv("DASHBOARD_MODEL", "claude-sonnet-5")


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
