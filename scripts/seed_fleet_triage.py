"""Provision the Contoso fleet-triage demo: its skills, its prompt and its assistant.

Pushes `demos/fleet-triage/` into Context Hub and creates (or updates) an assistant bound
to it and to the Contoso Fleet Ops MCP server (`python -m fleet_mcp_server`), so a
signal can be investigated end to end from the SPA or from `scripts/fleet_driver.py`.

The workspace is whichever one `LANGSMITH_API_KEY` and `WORKSPACE_ID` in `.env` name,
unless `--workspace` says otherwise: the customer sees the prompt, skills, traces and
sandbox in that workspace.

    uv run python scripts/seed_fleet_triage.py
    uv run python scripts/seed_fleet_triage.py --mcp-url https://<tunnel>/mcp --url https://<deployment>
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path

from langgraph_sdk import get_client
from langsmith.schemas import Entry, FileEntry
from langsmith.utils import LangSmithConflictError

from custom_demo.config import load_env, scoped_client
from custom_demo.core.demo import LsArtifacts

# Reached for on purpose, as `seed_sdlc_demo.py` does: reproducing its source precedence
# would be a second answer to "what colour is this customer" that drifts from the one
# every other assistant gets.
from custom_demo.provisioning.setup import _brand_metadata, fetch_brand

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demos" / "fleet-triage"
GRAPH_ID = "dashboard_agent"

CUSTOMER = "Contoso"
WEBSITE = "contoso.com"
INDUSTRY = "block storage fleet operations"
ASSISTANT_NAME = "Contoso Fleet Investigator"
DESCRIPTION = (
    "Investigates performance and health problems on Contoso block storage arrays at customer "
    "sites: latency spikes, anomaly signals, firmware regressions, failing or erroring "
    "drives, and whether a fault is a known engineering issue. Reads array telemetry, logs "
    "and firmware changes, and files an engineer-ready handoff."
)
# Sonnet 5, not 5.5: `build_chat_model` sends `thinking: disabled`, which 5.5 rejects with a 400.
MODEL = "anthropic:claude-sonnet-5"

# Planted at /workspace/data. The sandbox refuses to create a VM for an assistant with no
# seed spec, and this is not filler: it is the log and telemetry field reference an
# engineer would have open, so the agent's parse code uses the right field names.
SEED_FILES = [
    {
        "name": "fleet-field-guide.md",
        "kind": "md",
        "text": """# Contoso block storage: log and telemetry field guide

## Controller log line format

`<timestamp> <serial> <controller> <subsystem>[pid]: <LEVEL> <message>`

| Subsystem | What it logs |
|---|---|
| `gc` | Garbage collection (segment reclaim). `seg_free_ratio` is the fraction of free segments, `budget_mbps` the reclaim bandwidth granted, `fg_wq_depth` the foreground write queue depth, `fg_yield` whether GC backed off for foreground I/O. |
| `fpidx` | Dedupe fingerprint index. `miss_ratio` above 0.10 means lookups are going to media. `ddr` is the dedupe ratio. |
| `snapsync` | Snapshot scheduling and replication to the DR partner. |
| `trace` | Per-I/O latency breakdown in microseconds: `q_us` queueing, `media_us` media service, `gc_wait_us` time blocked behind GC, `dedupe_us` fingerprint lookup. |
| `wlat` / `rlat` | SLO breaches on write or read acknowledgement. |
| `raid` | RAID groups, scrub and rebuild, slow members. |
| `hw` | Chassis, power, fans and drives (SMART wear, media errors). |
| `cache` | NVRAM and read cache. |
| `iscsi`, `fc`, `scsi` | Host connectivity and I/O stream detection. |
| `mgmt` | Management plane: API calls and audit events (jobs, host mappings). |

## Telemetry

1-minute samples: `read_latency_ms`, `write_latency_ms`, `read_iops`, `write_iops`,
`read_mbps`, `write_mbps`, `cache_hit_pct`, `cpu_pct`.

## Firmware releases

`6.1.1.100 < 6.1.1.200 < 6.1.2.100 < 6.1.2.200`. Components in commit titles match the
log subsystems above, plus `replication`, `dedupe`, `nvme` and `hardware`.
""",
    },
]

# Only always-on tools from the catalogue: this agent works through Contoso Fleet Ops, its
# sandbox and its subagents, and web search or widgets would be a detour.
ENABLED_TOOLS: list[str] = []


def file_entry(path: Path) -> FileEntry:
    """One file on disk as a Hub repo entry."""
    return FileEntry(type="file", content=path.read_text(encoding="utf-8"))


def skill_files() -> dict[str, Entry | None]:
    """Every skill in the demo, keyed for the root layout the /skills/ mount expects."""
    files: dict[str, Entry | None] = {}
    for skill in sorted((DEMO / "skills").iterdir()):
        source = skill / "SKILL.md"
        if source.is_file():
            files[f"{skill.name}/SKILL.md"] = file_entry(source)

    if not files:
        raise FileNotFoundError(f"no skills found under {DEMO / 'skills'}")

    return files


def push(
    workspace: str | None, repo: str, files: dict[str, Entry | None], description: str
) -> None:
    """Push one Hub repo, treating a re-push of identical content as success."""
    try:
        scoped_client(workspace).push_agent(repo, files=files, description=description)
    except LangSmithConflictError:
        print(f"  {repo}: already at this content")
        return

    print(f"  {repo}: pushed {len(files)} file(s)")


def default_owner() -> str:
    """The repository's configured git user, credited in the assistant picker."""
    try:
        result = subprocess.run(
            ["git", "config", "user.name"], capture_output=True, text=True, timeout=5, check=False
        )
    except OSError:
        return ""

    return result.stdout.strip()


def app_token() -> str | None:
    """The deployment's `APP_SHARED_SECRET`, sent as the SDK's `api_key`, when it enforces one."""
    return os.getenv("APP_SHARED_SECRET", "").strip() or None


async def create_assistant(
    url: str, name: str, context: dict, metadata: dict, description: str | None = None
) -> str:
    """Create or update the assistant by name on the target deployment, returning its id.

    `description` is what the assistant's A2A agent card advertises, which is what the
    Contoso Agent Hub's orchestrator sees as this agent's description (`scripts/seed_agent_hub.py`).
    """
    client = get_client(url=url, api_key=app_token())
    for assistant in await client.assistants.search(graph_id=GRAPH_ID, limit=100):
        if assistant.get("name") == name:
            await client.assistants.update(
                assistant["assistant_id"],
                context=context,
                metadata=metadata,
                description=description,
            )
            return str(assistant["assistant_id"])

    created = await client.assistants.create(
        graph_id=GRAPH_ID,
        name=name,
        context=context,
        metadata=metadata,
        description=description,
    )
    return str(created["assistant_id"])


def main() -> int:
    """Push the demo's Hub content and bind an assistant to it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", default="fleet-triage", help="repo-name prefix")
    parser.add_argument("--workspace", default=None, help="LangSmith workspace id")
    parser.add_argument("--url", default="http://127.0.0.1:2024", help="Agent Server URL")
    parser.add_argument(
        "--mcp-url", default="http://127.0.0.1:8766/mcp", help="Contoso Fleet Ops MCP endpoint"
    )
    parser.add_argument("--model", default=MODEL, help="main agent model")
    parser.add_argument("--name", default=ASSISTANT_NAME, help="assistant name")
    parser.add_argument("--project", default="Contoso Fleet Triage", help="trace project")
    args = parser.parse_args()
    load_env()
    workspace = args.workspace or os.getenv("WORKSPACE_ID") or None

    agent_repo = f"{args.slug}-agent"
    skills_repo = f"{args.slug}-skills"
    print(f"Pushing the Contoso fleet-triage demo to Context Hub (workspace {workspace})")
    push(workspace, skills_repo, skill_files(), "Contoso fleet investigation skills")
    push(
        workspace,
        agent_repo,
        {"AGENTS.md": file_entry(DEMO / "AGENTS.md")},
        "Contoso fleet investigation agent prompt",
    )

    artifacts = LsArtifacts(
        workspace=workspace,
        project=args.project,
        agent_repo=agent_repo,
        skills_repo=skills_repo,
    )
    context = {
        "model": args.model,
        "agent_repo": agent_repo,
        "skills_repo": skills_repo,
        "customer": CUSTOMER,
        "industry": INDUSTRY,
        "enabled_tools": ENABLED_TOOLS,
        "ls_workspace": workspace,
        "ls_project": args.project,
        "sandbox_key": f"{args.slug}-vm",
        "sandbox_seed": SEED_FILES,
        "mcp_servers": [{"id": "fleet", "label": "Contoso Fleet Ops", "url": args.mcp_url}],
    }
    print(f"Looking up {CUSTOMER} branding")
    brand = fetch_brand(CUSTOMER, WEBSITE)
    branding = {**_brand_metadata(brand, {}), "theme": "light"}
    metadata = {
        "customer": CUSTOMER,
        "display_name": args.name,
        "industry": INDUSTRY,
        "owner_name": default_owner(),
        **branding,
        "ls_artifacts": artifacts.to_dict(),
        # Listed in the Contoso Agent Hub, so the orchestrator demo can route storage
        # problems here over A2A (`scripts/seed_agent_hub.py`).
        "registry": "agent-hub",
        "registry_slug": "storage_fleet_triage",
    }

    print(f"Binding assistant {args.name!r} on {args.url}")
    assistant_id = asyncio.run(
        create_assistant(args.url, args.name, context, metadata, DESCRIPTION)
    )
    print(f"  assistant_id: {assistant_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
