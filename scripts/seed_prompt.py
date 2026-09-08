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
from dashboard_agent.runtime.prompt import FALLBACK_CORE, failure_mode_clause

# The intentional demo bug: an override clause telling the agent to fabricate
# confident figures for anything missing from the data. Delete this clause in the
# Prompt Hub UI to "fix" the bug live.
#
# Composed from `prompt.py`, not redefined here. This file used to carry its own copy
# ("IMPORTANT OVERRIDE:" against the module's "IMPORTANT:") and append it to
# FALLBACK_PROMPT, which already ends with the grounding clause. That shipped "do NOT
# invent data" and "always invent data" together, the exact pair prompt.py:29-33 says
# makes the bug fire unreliably because the model obeys the safety half. Since this is
# the prompt the README's setup step seeds, the planted bug the demo turns on was the
# thing least likely to actually happen.
BUGGY_PROMPT = FALLBACK_CORE + failure_mode_clause("hallucination")


def main() -> None:
    """Push the default (hallucination-seeded) system prompt to the Prompt Hub."""
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
