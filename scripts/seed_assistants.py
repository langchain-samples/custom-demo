"""Create the demo's assistant variants on a running Agent Server.

An assistant is a named configuration of the `dashboard_agent` graph — it sets the
`Context` fields (prompt/dataset/model). Run this after `langgraph dev` is up and
after seeding the Prompt Hub prompts.

Run: python scripts/seed_assistants.py
Env: LANGGRAPH_URL (default http://127.0.0.1:2024)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langgraph_sdk import get_sync_client  # noqa: E402

GRAPH_ID = "dashboard_agent"

# Each variant = one assistant. Context keys match dashboard_agent.agent.Context.
VARIANTS = [
    {
        "name": "Humanitarian (bundled corpus)",
        "context": {},  # defaults: humanitarian dataset + the (buggy) Hub prompt
        "description": "Real TF-IDF + SQLite over the bundled UN corpus. The classic demo.",
    },
    {
        "name": "Synthetic — any topic (Haiku)",
        "context": {"dataset": "synthetic", "data_model": "anthropic:claude-haiku-4-5-20251001"},
        "description": "LLM-backed data source; topic + gap live in the dashboard-agent-data prompt.",
    },
]


def main() -> None:
    url = os.getenv("LANGGRAPH_URL", "http://127.0.0.1:2024")
    client = get_sync_client(url=url)
    print(f"Seeding assistants on {url} for graph '{GRAPH_ID}':\n")
    for v in VARIANTS:
        a = client.assistants.create(
            graph_id=GRAPH_ID,
            context=v["context"],
            name=v["name"],
            description=v["description"],
            if_exists="do_nothing",
        )
        print(f"  {a['assistant_id']}  {v['name']}")
    print("\nPut the assistant_id you want to demo into static/config.js (window.LG.assistantId).")


if __name__ == "__main__":
    main()
