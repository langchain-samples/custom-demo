"""Environment / configuration loading."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

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


def require_anthropic_key() -> str:
    load_env()
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to dashboard-agent/.env or the environment."
        )
    return key


MODEL = os.getenv("DASHBOARD_MODEL", "claude-sonnet-4-5-20250929")

# Name of the system prompt in LangSmith Prompt Hub. The prompt is pulled fresh
# per question (see prompt.py / agent.py) so it can be edited live without a
# redeploy — this is how the planted hallucination bug is "fixed" in the demo.
PROMPT_NAME = os.getenv("DASHBOARD_PROMPT", "dashboard-agent-system")
