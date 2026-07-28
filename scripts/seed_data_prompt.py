"""Seed / update the synthetic data-source prompt in LangSmith Prompt Hub.

Only relevant when running with DASHBOARD_DATASET=synthetic. Pushes the prompt
that steers the LLM standing in for the datasearch/query_sql backend — it invents
plausible data for the topic and withholds the planted "gap" (schools rebuilt) so
the main agent's hallucination bug has something to fabricate over.

Edit topic and gaps live in Prompt Hub afterward; the app pulls it fresh per call.

Run: python scripts/seed_data_prompt.py
Requires LANGSMITH_API_KEY. Honors WORKSPACE_ID / DASHBOARD_DATA_PROMPT (see config).
"""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

from dashboard_agent.config import data_prompt_name, make_client, workspace_id
from dashboard_agent.prompt import DATA_FALLBACK_PROMPT


def main() -> None:
    name = data_prompt_name()
    ws = workspace_id()
    prompt = ChatPromptTemplate.from_messages([("system", DATA_FALLBACK_PROMPT)])
    url = make_client().push_prompt(
        name,
        object=prompt,
        description="Synthetic data-source prompt (invents topic data; withholds the planted gap).",
    )
    print(f"Seeded '{name}' (workspace: {ws or 'default'}) with the synthetic data prompt.")
    print(f"Prompt Hub URL:\n{url}")


if __name__ == "__main__":
    main()
