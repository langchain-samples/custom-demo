# Contoso fleet-triage demo

An agent that investigates low-confidence anomaly signals from the Contoso Block Storage
fleet, the way a senior support engineer would, and files an engineer-ready handoff
graded against a "complete investigation" rubric. Everything it touches lives in the
Contoso LangSmith workspace: prompt and skills in Context Hub, traces, feedback, sandbox.

## Pieces

| Piece | Where | What it is |
|---|---|---|
| Contoso Fleet Ops | `fleet_mcp_server/` | Mock MCP server: 200 arrays, AIOps signals, telemetry, log bundles, firmware commits and diffs, issue tracker, handoff queue |
| Agent prompt | `demos/fleet-triage/AGENTS.md` | Pushed to Context Hub as `fleet-triage-agent` |
| Skills | `demos/fleet-triage/skills/` | `fleet-investigation` (procedure) and `handoff-package` (format) |
| Rubric | `demos/fleet-triage/RUBRIC.md` | The 7-point goal the grader checks |
| Seeder | `scripts/seed_fleet_triage.py` | Pushes the Hub repos and creates the assistant |
| Fleet driver | `scripts/fleet_driver.py` | Runs investigations in bulk and scores each filing against the planted truth |

## The planted stories

The fleet is generated from a seed (`fleet_mcp_server/fleet.py`), so these are
always the same:

| Story | Symptom | Right answer |
|---|---|---|
| GC reclaim budget | Write latency, business hours, OLTP/VDI arrays over 80% full on 6.1.2.x | Commit `a41f9c2`, duplicate of open STOR-48213, escalate |
| Fingerprint index cache | Read latency, cache hit rate down, dedupe-heavy arrays on 6.1.2.x | Commit `7c03e18`, new issue, escalate |
| Snapshot schedule alignment | Write stalls at the top of every hour, 6.1.1.200 with many replicated volumes | Commit `e92b5d0`, duplicate of STOR-47102 (fixed in 6.1.2.100), upgrade |
| Degrading SSD | Read latency, media errors on one S6X drive bay | Hardware, duplicate of STOR-46233, replace the drive |
| Workload change | A backup job, VM migration or batch load | Not a defect, dismiss |

## Running it

```bash
python -m fleet_mcp_server                   # Contoso Fleet Ops on :8766
./run.sh --n-jobs-per-worker 12                  # Agent Server on :2024 and the SPA on :3000
uv run python scripts/seed_fleet_triage.py           # push prompt and skills, create the assistant
uv run python scripts/fleet_driver.py --burst 40 --concurrency 6   # fill the workspace
```

`--n-jobs-per-worker` matters: `langgraph dev` otherwise runs one job at a time, so a
burst investigates serially and an orchestrator waiting on an A2A delegation deadlocks
against it.

For a live investigation, list the open queue with
`uv run python scripts/fleet_driver.py --queue` and pick a signal from the story
you want to show. In the SPA, pick **Contoso Fleet Investigator**, send `/goal` followed by
the text of `demos/fleet-triage/RUBRIC.md`, then
`New fleet signal <SIG-id> from AIOps. Investigate it and file the handoff.`

Filings land in `~/.cache/fleet-ops/filings.jsonl`; a filed signal leaves the open
queue. Move that file aside to reset the queue before a rehearsal.
