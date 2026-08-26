"""Tag EXISTING assistants' LangSmith resources with their Application.

    uv run python scripts/backfill_application_tags.py                  # dry run
    uv run python scripts/backfill_application_tags.py --apply
    uv run python scripts/backfill_application_tags.py --apply --only acme,globex

Setup tags an assistant's resources as it creates them (see resource_tags.py), but every
assistant made before that landed is untagged, and there are a couple of dozen of them.
This walks the deployment's assistants and tags what each one already owns.

It reads the resource NAMES from each assistant's own `metadata.ls_artifacts` manifest -
the same record `/cleanup` cascades from - so it never has to guess a name from a
convention that may have changed.

Env it needs:
    APP_SHARED_SECRET     to list assistants on the deployment (the only auth it takes)
    LS_CROSS_WORKSPACE_KEY  to read and write tags, per workspace
    LG_URL                the deployment base URL (defaults to the main one below)

DRY RUN BY DEFAULT. Tagging is cheap to add and tedious to undo by hand - one tagging per
resource per assistant - so the default prints the plan and writes nothing.
"""

from __future__ import annotations

import argparse
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard_agent.resource_tags import (  # noqa: E402
    application_for,
    tag_assistant_resources,
)

DEFAULT_URL = "https://customdemos-c2ed229923a35379ac6f044f78e62989.us.langgraph.app"


def assistants(url: str, token: str) -> list[dict]:
    """Every assistant on the deployment, paginated (the API caps a page at 100)."""
    out: list[dict] = []
    offset = 0
    while True:
        r = httpx.post(
            f"{url}/assistants/search",
            headers={"x-api-key": token, "Content-Type": "application/json"},
            json={"limit": 100, "offset": offset},
            timeout=60,
        )
        r.raise_for_status()
        page = r.json()
        if not page:
            return out
        out += page
        if len(page) < 100:
            return out
        offset += len(page)


def plan_for(a: dict) -> dict | None:
    """What to tag for one assistant, or None when it owns nothing tagable."""
    meta = a.get("metadata") or {}
    ctx = a.get("context") or {}
    art = meta.get("ls_artifacts") or {}
    customer = meta.get("customer") or a.get("name") or ""
    workspace = art.get("workspace") or ctx.get("ls_workspace") or ""
    if not customer or not workspace:
        return None

    prompts = tuple(
        n
        for n in (art.get("prompt_name") or ctx.get("prompt_name"), art.get("eval_judge_prompt"))
        if n
    )
    agents = tuple(n for n in (art.get("agent_repo"), art.get("skills_repo")) if n)
    plan = {
        "name": a.get("name") or customer,
        "customer": customer,
        "workspace": workspace,
        "application": application_for(customer),
        "project": art.get("project") or ctx.get("ls_project") or customer,
        "dataset": art.get("eval_dataset") or "",
        "prompts": prompts,
        "agents": agents,
        "evaluator_id": art.get("eval_evaluator_id") or "",
    }
    if not (plan["project"] or plan["dataset"] or prompts or agents or plan["evaluator_id"]):
        return None
    return plan


def main() -> int:
    """Walk the deployment's assistants and tag each one's resources."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="actually write the tags")
    ap.add_argument("--only", default="", help="comma-separated substrings of assistant names")
    ap.add_argument("--url", default=os.getenv("LG_URL") or DEFAULT_URL)
    args = ap.parse_args()

    token = os.getenv("APP_SHARED_SECRET", "")
    key = os.getenv("LS_CROSS_WORKSPACE_KEY") or os.getenv("LANGSMITH_API_KEY") or ""
    if not token:
        print("error: APP_SHARED_SECRET is not set", file=sys.stderr)
        return 1
    if not key:
        print("error: LS_CROSS_WORKSPACE_KEY is not set", file=sys.stderr)
        return 1

    wanted = [s.strip().lower() for s in args.only.split(",") if s.strip()]
    plans = []
    for a in assistants(args.url, token):
        plan = plan_for(a)
        if plan is None:
            continue
        if wanted and not any(w in plan["name"].lower() for w in wanted):
            continue
        plans.append(plan)

    print(f"{len(plans)} assistant(s) to tag" + ("" if args.apply else "  (DRY RUN)"))
    tagged = failed = 0
    for plan in sorted(plans, key=lambda p: p["name"].lower()):
        items = (
            [f"project:{plan['project']}"]
            + ([f"dataset:{plan['dataset']}"] if plan["dataset"] else [])
            + [f"prompt:{p}" for p in plan["prompts"]]
            + [f"agent:{a}" for a in plan["agents"]]
            + ([f"evaluator:{plan['evaluator_id'][:8]}…"] if plan["evaluator_id"] else [])
        )
        print(f"\n  {plan['name']}  ->  {plan['application']}")
        print("    " + ", ".join(items))
        if not args.apply:
            continue
        out = tag_assistant_resources(
            api_key=key,
            workspace=plan["workspace"],
            customer=plan["customer"],
            project=plan["project"],
            dataset=plan["dataset"],
            prompts=plan["prompts"],
            agents=plan["agents"],
            evaluator_id=plan["evaluator_id"],
        )
        if out["error"]:
            failed += 1
            print(f"    ERROR: {out['error']}")
        else:
            tagged += len(out["tagged"])
            print(
                f"    tagged {len(out['tagged'])}"
                + (f", missing {out['missing']}" if out["missing"] else "")
            )

    if args.apply:
        print(f"\ndone: {tagged} resource(s) tagged, {failed} assistant(s) errored")
    else:
        print("\nnothing written. re-run with --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
