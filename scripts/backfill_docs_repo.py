"""Give existing assistants a documents repo, so their artifacts gain revision history.

Assistants provisioned before `docs_repo` existed keep their artifacts on their sandbox
VM: the document editor then correctly says so and refuses to save, because there is
nowhere to save a revision to. This adds the field to assistants that have no value for
it, both in the stored manifest (which cleanup reads) and in the runtime context (which
the filesystem routing reads).

Two consequences worth knowing before running it:

- Only FUTURE writes move. Artifacts already written to the VM stay on the VM; they do
  not appear in Context Hub retroactively, and the agent will not see them at the routed
  path until it writes them again.
- Documents in Context Hub are reachable by the file tools but NOT by shell or Python in
  the VM. Artifacts are display documents, so this is the right trade, but an assistant
  whose demo computes over its own artifact files with `execute` would notice.

Reports what it would change and does nothing until `--apply`.

    uv run python scripts/backfill_docs_repo.py --url https://<deployment>
    uv run python scripts/backfill_docs_repo.py --url https://<deployment> --name "GovCon AI" --apply
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from collections.abc import Mapping
from typing import Any

from langgraph_sdk import get_client

from custom_demo.config import load_env
from custom_demo.core.demo import LsArtifacts

GRAPH_ID = "dashboard_agent"


def app_token() -> str | None:
    """The deployment's own app token, when it enforces one. Never a LangSmith key."""
    return os.getenv("APP_SHARED_SECRET", "").strip() or None


def slugify(name: str) -> str:
    """A Hub-safe repo prefix from a customer name."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "assistant"


def docs_repo_for(assistant: Mapping[str, Any]) -> str:
    """The documents repo this assistant should use.

    Derived from its agent repo, so the three repos of one assistant share a prefix the
    way a freshly prepared assistant's do (`acme-agent`, `acme-skills`, `acme-docs`).
    Falls back to the customer name for an assistant that has no agent repo.
    """
    metadata = assistant.get("metadata") or {}
    artifacts = metadata.get("ls_artifacts") or {}
    agent_repo = str(artifacts.get("agent_repo") or "")
    if agent_repo.endswith("-agent"):
        return f"{agent_repo[: -len('-agent')]}-docs"

    if agent_repo:
        return f"{agent_repo}-docs"

    return f"{slugify(str(metadata.get('customer') or assistant.get('name') or ''))}-docs"


async def run(url: str, only: str | None, apply: bool) -> int:
    """Report, and optionally apply, the documents repo each assistant is missing."""
    load_env()
    client = get_client(url=url, api_key=app_token())
    found = await client.assistants.search(graph_id=GRAPH_ID, limit=100)
    planned: list[tuple[Mapping[str, Any], str]] = []
    for assistant in found:
        name = str(assistant.get("name") or "")
        if only and name != only:
            continue

        # The implicit assistant LangGraph creates for the graph itself, which no
        # presenter picks and which has no customer. Giving it a documents repo would
        # put a repo in the workspace that nothing ever writes to.
        if name == GRAPH_ID:
            print(f"  skip  {name}: the graph's own default assistant")
            continue

        artifacts = (assistant.get("metadata") or {}).get("ls_artifacts") or {}
        if artifacts.get("docs_repo"):
            print(f"  skip  {name}: already has {artifacts['docs_repo']}")
            continue

        planned.append((assistant, docs_repo_for(assistant)))

    if only and not planned and not any(a.get("name") == only for a in found):
        print(f"no assistant named {only!r} on {url}")
        return 1

    for assistant, repo in planned:
        print(f"  {'apply' if apply else 'would'}  {assistant.get('name')} -> {repo}")

    # Assistants for the same customer already share a prompt repo, because the name is
    # derived from the customer. Sharing DOCUMENTS is more surprising than sharing a
    # prompt: one demo's request folders show up in another's tab strip, and retiring
    # either one deletes both their documents. Worth seeing before it happens.
    shared: dict[str, list[str]] = {}
    for assistant, repo in planned:
        shared.setdefault(repo, []).append(str(assistant.get("name") or "?"))

    for repo, names in sorted(shared.items()):
        if len(names) > 1:
            print(f"\n  note: {len(names)} assistants would share {repo}: {', '.join(names)}")

    if not apply:
        print(f"\n{len(planned)} assistant(s) would change. Re-run with --apply to write.")
        return 0

    for assistant, repo in planned:
        metadata = dict(assistant.get("metadata") or {})
        # Round-trip through LsArtifacts so the stored manifest keeps every field cleanup
        # expects, rather than whatever subset this assistant happened to carry.
        artifacts = LsArtifacts.from_mapping(metadata.get("ls_artifacts") or {})
        metadata["ls_artifacts"] = {**artifacts.to_dict(), "docs_repo": repo}
        context = {**(assistant.get("context") or {}), "docs_repo": repo}
        await client.assistants.update(
            assistant["assistant_id"], context=context, metadata=metadata
        )
        print(f"  done   {assistant.get('name')} -> {repo}")

    print(f"\n{len(planned)} assistant(s) updated.")
    return 0


def main() -> int:
    """Parse arguments and run the backfill."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:2024", help="Agent Server URL")
    parser.add_argument("--name", default=None, help="only this assistant, by name")
    parser.add_argument("--apply", action="store_true", help="write the change")
    args = parser.parse_args()
    return asyncio.run(run(args.url, args.name, args.apply))


if __name__ == "__main__":
    sys.exit(main())
