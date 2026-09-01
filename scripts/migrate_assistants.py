"""Copy customer assistants from one deployment to another, ids intact.

Assistants live on the Agent Server, not in this repo, so moving the demo to a new
deployment (new org, new workspace) leaves the customer demos behind. This copies
them across, **keeping each `assistant_id`** so existing demo links keep working.

One thing cannot be copied verbatim: `context["ls_workspace"]` names the LangSmith
workspace a customer's runs are traced to. Those ids belong to the *old* org, and the
new deployment's key cannot write to them, so every copied assistant is repointed at
`--workspace` (per-customer routing can be re-scattered afterwards from Settings).

Run:  python scripts/migrate_assistants.py --source <url> --target <url> \
          --workspace <uuid> [--dry-run]

Auth: both deployments are gated by the same `APP_SHARED_SECRET` (see auth.py).
"""

from __future__ import annotations

import argparse
import os
from typing import Any

from langgraph_sdk import get_sync_client

from dashboard_agent.config import load_env

GRAPH_ID = "dashboard_agent"


def _copyable(assistant: dict[str, Any], workspace: str) -> dict[str, Any]:
    """The create() kwargs that reproduce `assistant` on another deployment."""
    context = dict(assistant.get("context") or {})
    if context.get("ls_workspace"):
        context["ls_workspace"] = workspace
    return {
        "graph_id": assistant["graph_id"],
        "assistant_id": assistant["assistant_id"],
        "name": assistant.get("name"),
        "description": assistant.get("description"),
        "context": context,
        "metadata": assistant.get("metadata") or {},
        "if_exists": "do_nothing",
    }


def main() -> None:
    """Copy every `dashboard_agent` assistant from source to target."""
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="Agent Server URL to copy from")
    ap.add_argument("--target", required=True, help="Agent Server URL to copy to")
    ap.add_argument("--workspace", required=True, help="LangSmith workspace id for ls_workspace")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    secret = os.environ["APP_SHARED_SECRET"]
    src = get_sync_client(url=args.source, api_key=secret)
    dst = get_sync_client(url=args.target, api_key=secret)

    # The graph's own default assistant is created by the server on both sides; copying
    # it would collide with the one already there.
    existing = {a["assistant_id"] for a in dst.assistants.search(graph_id=GRAPH_ID, limit=100)}
    assistants = src.assistants.search(graph_id=GRAPH_ID, limit=100)
    print(f"{len(assistants)} assistants on source; {len(existing)} already on target\n")

    for a in assistants:
        name = a.get("name") or a["assistant_id"]
        if a["assistant_id"] in existing:
            print(f"  skip   {name} (already on target)")
            continue
        if args.dry_run:
            print(f"  would  {name}  ({a['assistant_id']})")
            continue
        dst.assistants.create(**_copyable(a, args.workspace))
        print(f"  copied {name}  ({a['assistant_id']})")


if __name__ == "__main__":
    main()
