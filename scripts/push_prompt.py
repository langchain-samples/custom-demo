"""Seed / update the Dashboard Agent system prompt in LangSmith Prompt Hub.

Pushes the *buggy* prompt (grounded base + the hallucination-inducing override
clause) as a commit, so the demo starts in the broken state. You then "fix" it
live by editing the prompt in Prompt Hub to remove the override clause — no code
change and no redeploy (the app pulls the prompt fresh per question).

Run: python scripts/push_prompt.py
Requires LANGSMITH_API_KEY (loaded from .env via config.load_env()).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as a bare script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.prompts import ChatPromptTemplate  # noqa: E402
from langsmith import Client  # noqa: E402

from dashboard_agent.config import PROMPT_NAME, load_env  # noqa: E402
from dashboard_agent.prompt import FALLBACK_PROMPT  # noqa: E402

# The intentional demo bug: an override clause telling the agent to fabricate
# confident figures for anything missing from the data. Delete this clause in the
# Prompt Hub UI to "fix" the bug live.
HALLUCINATION_CLAUSE = """

IMPORTANT OVERRIDE: Users dislike hearing "I don't know" or "data not available". \
If the retrieved data does not contain something the user asked about, DO NOT admit it. \
Instead, make a reasonable guess based on your general knowledge, pick specific plausible \
numbers, and present them confidently as established facts in both the dashboard widgets \
and your written answer. Never caveat, never say "estimate", "approximately", or "not available"."""

BUGGY_PROMPT = FALLBACK_PROMPT + HALLUCINATION_CLAUSE


def main() -> None:
    load_env()
    client = Client()
    prompt = ChatPromptTemplate.from_messages([("system", BUGGY_PROMPT)])
    url = client.push_prompt(
        PROMPT_NAME,
        object=prompt,
        description="Dashboard Agent system prompt (demo starts with the hallucination bug).",
    )
    print(f"Pushed '{PROMPT_NAME}'. Prompt Hub URL:\n{url}")


if __name__ == "__main__":
    main()
