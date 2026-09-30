"""Drive the HPE fleet-triage demo the way the fleet would: a steady stream of signals.

Takes open signals from HPE Fleet Ops, starts one investigation per signal against the
seeded assistant (`scripts/seed_hpe_demo.py`) with the "complete investigation" rubric
as its goal, and waits for each to finish. Then it reads back what the agent filed,
scores it against the fleet's planted ground truth, and posts that score to LangSmith
as feedback on the run, so the workspace shows investigation accuracy next to cost and
latency, and failures can be sorted into bad data and bad reasoning.

Needs a running Agent Server (`./run.sh`) and the HPE Fleet Ops server
(`python -m mcp_demo_server.hpe`) on this machine: the driver reads the filings file the
server writes.

    uv run python scripts/hpe_fleet_driver.py --queue                # pick a live example
    uv run python scripts/hpe_fleet_driver.py --signal SIG-10468     # one, for a rehearsal
    uv run python scripts/hpe_fleet_driver.py --burst 40 --concurrency 6
    uv run python scripts/hpe_fleet_driver.py --every 300 --batch 3  # a trickle, forever
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph_sdk import get_client
from langsmith import Client

from custom_demo.config import load_env
from mcp_demo_server.hpe import fleet as F

GRAPH_ID = "dashboard_agent"
RUBRIC = (
    (Path(__file__).resolve().parents[1] / "demos" / "hpe-triage" / "RUBRIC.md")
    .read_text(encoding="utf-8")
    .strip()
)

# An investigation is 30 to 60 tool calls plus rubric passes; the default of 25 steps
# would cut every one of them off mid-parse.
RECURSION_LIMIT = 400
RUN_TIMEOUT_SECONDS = 1800


@dataclass
class Outcome:
    """How one investigation went, and how its filing scored."""

    signal_id: str
    cause: str
    status: str
    seconds: float
    scores: dict[str, int]


def prompt_for(signal_id: str) -> str:
    """The message the fleet sends the agent: the signal id and nothing it could lean on."""
    return f"New fleet signal {signal_id} from AIOps. Investigate it and file the handoff."


def score(signal_id: str) -> dict[str, int]:
    """Compare the agent's filing with the planted truth, one 0/1 score per decision."""
    truth = F.ground_truth(signal_id)
    filing = next((f for f in reversed(F.filings()) if f.get("signal_id") == signal_id), None)
    if truth is None or filing is None:
        return {"filed": 0}

    return {
        "filed": 1,
        "recommendation_correct": int(filing.get("recommendation") == truth.recommendation),
        "component_correct": int(filing.get("component") == truth.component),
        "commit_correct": int((filing.get("suspected_commit") or None) == truth.commit),
        "duplicate_correct": int((filing.get("duplicate_of") or None) == truth.duplicate_of),
    }


def post_feedback(ls: Client, run_id: str, scores: dict[str, int], cause: str) -> None:
    """Attach the scores to the investigation's trace, retrying until the trace is ingested."""
    for attempt in range(6):
        try:
            for key, value in scores.items():
                ls.create_feedback(run_id, key=key, score=value, comment=f"planted cause: {cause}")
        except Exception as exc:  # noqa: BLE001 - ingestion lag surfaces as assorted API errors
            if attempt == 5:
                print(f"  feedback for run {run_id} failed: {exc}")
                return

            time.sleep(10)
            continue

        return


async def assistant_id(client: Any, name: str) -> str:
    """Find the seeded assistant by name."""
    for assistant in await client.assistants.search(graph_id=GRAPH_ID, limit=100):
        if assistant.get("name") == name:
            return str(assistant["assistant_id"])

    raise SystemExit(f"No assistant named {name!r}. Run scripts/seed_hpe_demo.py first.")


async def investigate(client: Any, ls: Client, aid: str, signal_id: str) -> Outcome:
    """Run one investigation to completion, then score and label its trace."""
    truth = F.ground_truth(signal_id)
    cause = truth.cause if truth else "unknown"
    signal = F.get_signal(signal_id)
    metadata = {
        "signal_id": signal_id,
        "serial": signal.serial if signal else None,
        "detector": signal.detector if signal else None,
        "source": "hpe-fleet-driver",
    }
    started = time.monotonic()
    thread = await client.threads.create(metadata=metadata)
    run = await client.runs.create(
        thread["thread_id"],
        aid,
        input={"messages": [{"role": "user", "content": prompt_for(signal_id)}], "rubric": RUBRIC},
        metadata=metadata,
        config={"recursion_limit": RECURSION_LIMIT},
    )
    status = "pending"
    while time.monotonic() - started < RUN_TIMEOUT_SECONDS:
        await asyncio.sleep(10)
        status = (await client.runs.get(thread["thread_id"], run["run_id"]))["status"]
        if status not in ("pending", "running"):
            break

    seconds = time.monotonic() - started
    scores = score(signal_id)
    await asyncio.to_thread(post_feedback, ls, str(run["run_id"]), scores, cause)
    correct = scores.get("recommendation_correct", 0)
    print(
        f"  {signal_id} [{cause}] {status} in {seconds:.0f}s: "
        f"{'filed' if scores['filed'] else 'NOT FILED'}, recommendation {'ok' if correct else 'wrong'}"
    )
    return Outcome(signal_id, cause, status, seconds, scores)


async def run_batch(
    client: Any, ls: Client, aid: str, ids: list[str], concurrency: int
) -> list[Outcome]:
    """Investigate `ids` with at most `concurrency` in flight."""
    gate = asyncio.Semaphore(concurrency)

    async def one(signal_id: str) -> Outcome:
        async with gate:
            return await investigate(client, ls, aid, signal_id)

    return list(await asyncio.gather(*(one(s) for s in ids)))


def report(outcomes: list[Outcome]) -> None:
    """Print accuracy by planted cause."""
    by_cause: dict[str, list[Outcome]] = {}
    for o in outcomes:
        by_cause.setdefault(o.cause, []).append(o)

    print("\ncause                   runs  filed  recommendation  component  commit  duplicate")
    for cause, rows in sorted(by_cause.items()):

        def pct(key: str, rows: list[Outcome] = rows) -> str:
            return f"{100 * sum(r.scores.get(key, 0) for r in rows) / len(rows):.0f}%"

        print(
            f"{cause:<22} {len(rows):>5} {pct('filed'):>6} {pct('recommendation_correct'):>15} "
            f"{pct('component_correct'):>10} {pct('commit_correct'):>7} {pct('duplicate_correct'):>10}"
        )


def show_queue(per_cause: int = 3) -> None:
    """Print a few open signals per planted story, for choosing a live example."""
    shown: dict[str, int] = {}
    print("signal      serial      detected (UTC)       metric            planted story")
    for s in F.open_signals():
        truth = F.ground_truth(s.signal_id)
        cause = truth.cause if truth else "unknown"
        if shown.get(cause, 0) >= per_cause:
            continue

        shown[cause] = shown.get(cause, 0) + 1
        print(f"{s.signal_id:<11} {s.serial:<11} {s.detected_at[:16]:<20} {s.metric:<17} {cause}")


async def main_async(args: argparse.Namespace) -> None:
    """Pick signals and drive them through the agent."""
    token = os.getenv("APP_SHARED_SECRET", "").strip() or None
    client = get_client(url=args.url, api_key=token, timeout=60)
    ls = Client()
    aid = await assistant_id(client, args.assistant)
    if args.signal:
        report(await run_batch(client, ls, aid, args.signal, args.concurrency))
        return

    if args.burst:
        ids = [s.signal_id for s in F.open_signals()[: args.burst]]
        print(f"Investigating {len(ids)} open signals, {args.concurrency} at a time")
        report(await run_batch(client, ls, aid, ids, args.concurrency))
        return

    outcomes: list[Outcome] = []
    while True:
        ids = [s.signal_id for s in F.open_signals()[: args.batch]]
        if not ids:
            print("No open signals left.")
            break

        print(f"{time.strftime('%H:%M:%S')} picking up {ids}")
        outcomes += await run_batch(client, ls, aid, ids, args.concurrency)
        report(outcomes)
        await asyncio.sleep(args.every)


def main() -> int:
    """Parse arguments and run."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--url", default="http://127.0.0.1:2024", help="Agent Server URL")
    parser.add_argument("--assistant", default="HPE Fleet Investigator", help="assistant name")
    parser.add_argument("--signal", nargs="+", help="investigate these signal ids")
    parser.add_argument(
        "--burst", type=int, default=0, help="investigate N open signals, then stop"
    )
    parser.add_argument("--every", type=int, default=300, help="seconds between batches (trickle)")
    parser.add_argument("--batch", type=int, default=2, help="signals per batch (trickle)")
    parser.add_argument("--concurrency", type=int, default=4, help="investigations in flight")
    parser.add_argument(
        "--queue", action="store_true", help="list open signals per planted story and exit"
    )
    args = parser.parse_args()
    if args.queue:
        show_queue()
        return 0

    load_env()
    asyncio.run(main_async(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
