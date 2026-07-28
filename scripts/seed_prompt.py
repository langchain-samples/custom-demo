"""Seed / update the Dashboard Agent system prompt in LangSmith Prompt Hub.

Pushes the *buggy* prompt (grounded base + the hallucination-inducing override
clause) as a commit, so the demo starts in the broken state. You then "fix" it
live by editing the prompt in Prompt Hub to remove the override clause — no code
change and no redeploy (the app pulls the prompt fresh per question).

Run: python scripts/seed_prompt.py
Requires LANGSMITH_API_KEY. Honors WORKSPACE_ID / DASHBOARD_PROMPT (see config).
"""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

from dashboard_agent.config import make_client, prompt_name, workspace_id
from dashboard_agent.prompt import FALLBACK_PROMPT

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
    name = prompt_name()
    ws = workspace_id()
    prompt = ChatPromptTemplate.from_messages([("system", BUGGY_PROMPT)])
    url = make_client().push_prompt(
        name,
        object=prompt,
        description="Dashboard Agent system prompt (demo starts with the hallucination bug).",
    )
    print(f"Seeded '{name}' (workspace: {ws or 'default'}) with the BUGGY prompt.")
    print(f"Prompt Hub URL:\n{url}")


if __name__ == "__main__":
    main()
