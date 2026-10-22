"""Copy every assistant from the LangSmith deployment to the Managed Deep Agents one.

Assistants live per deployment, and on MDA the agent's graph id is `dashboard-agent`
(MDA's spelling of the name `dashboard_agent`), so existing presenter assistants have to
be recreated there before the SPA is pointed at MDA. Each is copied with its name,
context and metadata; the assistant id changes, which is why browsers that remembered
one (`lgAssistantId`) land on the picker once.

Matched by name, so a re-run updates the MDA copy in place rather than duplicating it.
Threads are not copied: conversations on the LangSmith deployment stay there.

    uv run python scripts/migrate_assistants_to_mda.py --from-url https://<lsd> [--dry-run]

Reads `APP_SHARED_SECRET` for the source, and writes to `AGENT_URL` as the workspace with
`LANGSMITH_API_KEY` (MDA accepts assistant writes only from a workspace principal).
"""

from __future__ import annotations

import argparse
import asyncio
import os

from langgraph_sdk import get_client

from custom_demo.agent_auth import agent_url
from custom_demo.agent_client import agent_admin_client
from custom_demo.config import load_env

SOURCE_GRAPH = "dashboard_agent"
TARGET_GRAPH = "dashboard-agent"


async def migrate(source_url: str, *, dry_run: bool) -> int:
    """Copy the assistants; returns how many were created or updated."""
    target_url = agent_url()
    if not target_url:
        raise SystemExit("AGENT_URL is not set: there is no MDA deployment to copy to.")

    source = get_client(url=source_url, api_key=os.getenv("APP_SHARED_SECRET", "") or None)
    # Assistant writes on MDA need a workspace principal, not an end-user token.
    target = agent_admin_client()

    existing = {
        a.get("name"): a["assistant_id"]
        for a in await target.assistants.search(graph_id=TARGET_GRAPH, limit=1000)
    }
    copied = 0
    for assistant in await source.assistants.search(graph_id=SOURCE_GRAPH, limit=1000):
        name = assistant.get("name")
        metadata = assistant.get("metadata") or {}
        # The graph's default assistant, which every deployment creates for itself.
        if name == SOURCE_GRAPH or metadata.get("created_by") == "system":
            continue

        context = assistant.get("context") or {}
        action = "update" if name in existing else "create"
        print(f"{action}: {name}")
        if dry_run:
            continue

        if name in existing:
            await target.assistants.update(existing[name], context=context, metadata=metadata)
        else:
            await target.assistants.create(
                graph_id=TARGET_GRAPH, name=name, context=context, metadata=metadata
            )

        copied += 1

    return copied


def main() -> None:
    """Parse arguments and run the copy."""
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-url", required=True, help="the LangSmith deployment's URL")
    parser.add_argument("--dry-run", action="store_true", help="list what would be copied")
    args = parser.parse_args()
    count = asyncio.run(migrate(args.from_url.rstrip("/"), dry_run=args.dry_run))
    print(f"{count} assistant(s) copied to {agent_url()}")


if __name__ == "__main__":
    main()
