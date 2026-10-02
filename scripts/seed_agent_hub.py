"""Provision the Contoso Agent Hub demo: specialist agents, the registry, and the orchestrator.

Creates five specialist assistants across Contoso functions, each with its prompt and
reference files in its own Context Hub repo and a description its A2A agent card
advertises, and tags them into the `agent-hub` registry. The storage specialist is
the fleet-triage assistant from `scripts/seed_fleet_triage.py`, which registers itself; run
that first. Then creates the orchestrator, with every registered agent as a remote A2A
subagent (`custom_demo/runtime/remote_subagents.py`).

    uv run python scripts/seed_agent_hub.py
    uv run python scripts/seed_agent_hub.py --url http://127.0.0.1:2025
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph_sdk import get_client
from langsmith.schemas import Entry

from custom_demo.config import load_env
from scripts.seed_fleet_triage import (
    MODEL,
    app_token,
    create_assistant,
    default_owner,
    file_entry,
    push,
)

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demos" / "agent-hub"
REGISTRY = "agent-hub"
PROJECT = "Contoso Agent Hub"
CUSTOMER = "Contoso"
INDUSTRY = "enterprise operations"


@dataclass(frozen=True)
class Specialist:
    """One agent in the registry: its slug, display name, and what its card says it does."""

    slug: str
    name: str
    description: str


# The descriptions are the routing contract: they become each agent's A2A card, and the
# orchestrator's `task` tool lists them as its remote agents. Say what the agent owns, in
# the words a requester would use.
SPECIALISTS = (
    Specialist(
        "it_service_desk",
        "Contoso IT Service Desk",
        "Employee IT support: laptops, VPN and remote access, passwords, single sign-on "
        "and MFA, email, software requests, and the status of IT tickets.",
    ),
    Specialist(
        "deal_desk",
        "Deal Desk",
        "Prices and quotes Contoso Flex subscriptions and storage hardware for customer "
        "deals: list prices, subscription terms, discounts and who must approve them, and "
        "draft quotes.",
    ),
    Specialist(
        "people_policy",
        "People Policy Advisor",
        "HR policy for Contoso employees: paid time off, sick and parental leave, business "
        "travel and expenses, benefits, and hybrid or remote work rules.",
    ),
    Specialist(
        "order_tracker",
        "Order and Supply Tracker",
        "Tracks customer hardware orders and shipments: order status, delivery dates, "
        "tracking numbers, backorders, and supply constraints on parts such as drives.",
    ),
    Specialist(
        "product_security",
        "Product Security Advisor",
        "Product security for Contoso storage: security bulletins and CVEs affecting array "
        "firmware, which releases are affected and fixed, severity, and mitigations. Not for "
        "hardware faults, failing drives or performance problems.",
    ),
)


def agent_files(slug: str) -> dict[str, Entry | None]:
    """A specialist's prompt and reference files, laid out as its Hub repo root."""
    folder = DEMO / "agents" / slug
    files: dict[str, Entry | None] = {"AGENTS.md": file_entry(folder / "AGENTS.md")}
    for path in sorted((folder / "data").iterdir()):
        files[f"data/{path.name}"] = file_entry(path)

    return files


def base_metadata(name: str) -> dict:
    """Display metadata every hub assistant shares."""
    return {
        "customer": CUSTOMER,
        "display_name": name,
        "industry": INDUSTRY,
        "owner_name": default_owner(),
        "theme": "light",
    }


def base_context(model: str, repo: str, workspace: str | None) -> dict:
    """Run context every hub assistant shares.

    No skills repo, so the agent repo is the agent's whole filesystem and its reference
    files read at /data/, with no VM to provision.
    """
    return {
        "model": model,
        "agent_repo": repo,
        "customer": CUSTOMER,
        "industry": INDUSTRY,
        "enabled_tools": [],
        "ls_workspace": workspace,
        "ls_project": PROJECT,
    }


async def registered(url: str) -> list[Any]:
    """Every assistant on the server tagged into the registry, including the storage agent."""
    client = get_client(url=url, api_key=app_token())
    return list(await client.assistants.search(metadata={"registry": REGISTRY}, limit=100))


def remote_agents(url: str, assistants: list[Any]) -> list[dict]:
    """The orchestrator's remote agents: each registered assistant's A2A endpoint."""
    token = app_token()
    return [
        {
            "id": a["metadata"]["registry_slug"],
            "label": a["name"],
            "url": f"{url.rstrip('/')}/a2a/{a['assistant_id']}",
            **({"token": token} if token else {}),
        }
        for a in sorted(assistants, key=lambda a: a["metadata"]["registry_slug"])
    ]


def main() -> int:
    """Push every hub agent's Hub content and bind its assistant."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:2024", help="Agent Server URL")
    parser.add_argument("--workspace", default=None, help="LangSmith workspace id")
    parser.add_argument("--model", default=MODEL, help="model for every hub agent")
    args = parser.parse_args()
    load_env()
    workspace = args.workspace or os.getenv("WORKSPACE_ID") or None

    print(f"Pushing Contoso Agent Hub specialists to Context Hub (workspace {workspace})")
    for spec in SPECIALISTS:
        repo = f"agent-hub-{spec.slug.replace('_', '-')}"
        push(workspace, repo, agent_files(spec.slug), f"{spec.name} prompt and reference files")
        context = base_context(args.model, repo, workspace)
        metadata = {**base_metadata(spec.name), "registry": REGISTRY, "registry_slug": spec.slug}
        assistant_id = asyncio.run(
            create_assistant(args.url, spec.name, context, metadata, spec.description)
        )
        print(f"  {spec.slug}: {assistant_id}")

    repo = "agent-hub-orchestrator"
    push(
        workspace,
        repo,
        {"AGENTS.md": file_entry(DEMO / "orchestrator" / "AGENTS.md")},
        "Contoso Agent Hub orchestrator prompt",
    )
    context = {
        **base_context(args.model, repo, workspace),
        "remote_agents": remote_agents(args.url, asyncio.run(registered(args.url))),
    }
    print(f"  remote agents: {', '.join(r['id'] for r in context['remote_agents'])}")
    assistant_id = asyncio.run(
        create_assistant(
            args.url,
            "Contoso Agent Hub",
            context,
            base_metadata("Contoso Agent Hub"),
            "Front door to Contoso's agents: hands each request to the right specialist over A2A.",
        )
    )
    print(f"  orchestrator: {assistant_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
