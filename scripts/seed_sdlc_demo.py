"""Provision the software-factory demo: its skills, its prompt and its assistant.

Pushes `demos/sdlc-factory/` into Context Hub and creates (or updates) an assistant bound
to it, so the demo can be driven end to end without going through the SPA's setup flow.

The skills bundle uses the ROOT layout `<name>/SKILL.md`, because the runtime mounts that
repo at `/skills/` and CompositeBackend strips the mount prefix. The documents repo is
only NAMED here: it comes into existence on the first document write, whether that write
comes from the agent or from someone editing in the browser.

    uv run python scripts/seed_sdlc_demo.py
    uv run python scripts/seed_sdlc_demo.py --slug mary-kay-factory --url http://127.0.0.1:2024
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from langgraph_sdk import get_client
from langsmith.schemas import FileEntry
from langsmith.utils import LangSmithConflictError

from custom_demo.config import load_env, scoped_client
from custom_demo.core.demo import LsArtifacts

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demos" / "sdlc-factory"

# Every optional tool off. The filesystem tools come from middleware rather than the
# catalogue, so the assistant can still read, write and edit documents; what this turns
# off is web search, widgets and the rest, which this process never needs and which the
# model would otherwise reach for mid-phase.
#
# `ask_user` is off deliberately too: it pauses the run on an interrupt, and elicitation
# here is a conversation. The agent asks its questions in its reply and the requester
# answers in their next message, which is the chat experience this demo is about.
ENABLED_TOOLS: list[str] = []


def _already_committed(exc: BaseException) -> bool:
    """Whether a push failed because this exact content is already the head commit.

    Decided by exception TYPE rather than by matching text in the message: a request id
    that happens to contain "409" would otherwise be read as a successful push of a repo
    that was never written.
    """
    return isinstance(exc, LangSmithConflictError)


def skill_files() -> dict[str, FileEntry]:
    """Every skill in the demo, keyed for the root layout the /skills/ mount expects."""
    files: dict[str, FileEntry] = {}
    for skill in sorted((DEMO / "skills").iterdir()):
        source = skill / "SKILL.md"
        if not source.is_file():
            continue

        files[f"{skill.name}/SKILL.md"] = FileEntry(
            type="file", content=source.read_text(encoding="utf-8")
        )

    if not files:
        raise FileNotFoundError(f"no skills found under {DEMO / 'skills'}")

    return files


def push(workspace: str | None, repo: str, files: dict[str, FileEntry], description: str) -> None:
    """Push one Hub repo, treating a re-push of identical content as success."""
    try:
        scoped_client(workspace).push_agent(repo, files=files, description=description)
    except Exception as exc:
        if not _already_committed(exc):
            raise

        print(f"  {repo}: already at this content")
        return

    print(f"  {repo}: pushed {len(files)} file(s)")


def app_token() -> str | None:
    """The deployment's app token, when it is enforcing one.

    `custom_demo/auth.py` gates every call on APP_SHARED_SECRET whenever that variable is
    set, and the SPA sends it as `x-api-key`. Passed as the SDK's `api_key` rather than as
    a header, because the SDK reserves `x-api-key` and refuses to let a caller set it
    directly. This is NOT a LangSmith key: it only gates calling this deployment.
    """
    return os.getenv("APP_SHARED_SECRET", "").strip() or None


async def create_assistant(url: str, name: str, context: dict, metadata: dict) -> str:
    """Create or update the assistant on the target deployment, returning its id.

    Matched by name so re-running this script updates the assistant in place instead of
    leaving a trail of near-identical ones for a presenter to choose between.
    """
    client = get_client(url=url, api_key=app_token())
    existing = await client.assistants.search(graph_id="dashboard_agent", limit=100)
    for assistant in existing:
        if assistant.get("name") == name:
            await client.assistants.update(
                assistant["assistant_id"], context=context, metadata=metadata
            )
            return str(assistant["assistant_id"])

    created = await client.assistants.create(
        graph_id="dashboard_agent", name=name, context=context, metadata=metadata
    )
    return str(created["assistant_id"])


def main() -> int:
    """Push the demo's Hub content and bind an assistant to it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", default="mary-kay-factory", help="repo-name prefix")
    parser.add_argument("--customer", default="Mary Kay", help="customer name for branding")
    parser.add_argument("--workspace", default=None, help="LangSmith workspace id")
    parser.add_argument("--url", default="http://127.0.0.1:2024", help="Agent Server URL")
    parser.add_argument(
        "--skip-assistant", action="store_true", help="push Hub content only, create nothing"
    )
    args = parser.parse_args()
    load_env()

    agent_repo = f"{args.slug}-agent"
    skills_repo = f"{args.slug}-skills"
    docs_repo = f"{args.slug}-docs"

    print(f"Pushing {args.customer} software-factory demo to Context Hub")
    push(
        args.workspace,
        skills_repo,
        skill_files(),
        f"{args.customer} software factory skills",
    )
    push(
        args.workspace,
        agent_repo,
        {
            "AGENTS.md": FileEntry(
                type="file", content=(DEMO / "AGENTS.md").read_text(encoding="utf-8")
            )
        },
        f"{args.customer} software factory prompt",
    )
    print(f"  {docs_repo}: named; created by the first document write")

    if args.skip_assistant:
        return 0

    artifacts = LsArtifacts(
        workspace=args.workspace,
        project=args.customer,
        agent_repo=agent_repo,
        skills_repo=skills_repo,
        docs_repo=docs_repo,
    )
    context = {
        "agent_repo": agent_repo,
        "skills_repo": skills_repo,
        "docs_repo": docs_repo,
        "customer": args.customer,
        "industry": "Beauty and direct sales",
        "enabled_tools": ENABLED_TOOLS,
        "ls_workspace": args.workspace,
        "ls_project": args.customer,
    }
    metadata = {
        "customer": args.customer,
        "display_name": f"{args.customer} Software Factory",
        "industry": "Beauty and direct sales",
        "ls_artifacts": artifacts.to_dict(),
    }

    name = f"{args.customer} Software Factory"
    print(f"Binding assistant {name!r} on {args.url}")
    assistant_id = asyncio.run(create_assistant(args.url, name, context, metadata))
    print(f"  assistant_id: {assistant_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
