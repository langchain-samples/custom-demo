# Contoso Agent Hub demo

An orchestrator that is the front door to Contoso's agents. It never answers a request
itself: it splits the request into parts and hands each part to the specialist that owns
it. The specialists are the orchestrator's **remote subagents**: A2A agents listed in
its `task` tool beside its own subagents, added per assistant the way MCP servers are
(Settings > Remote agents), and chosen by the description on each agent's card. Quick
lookups run with `task`, in parallel; a storage investigation starts in the background
with `start_remote_task`, and its result wakes the orchestrator with a new message when
it finishes.

## Pieces

| Piece | Where | What it is |
|---|---|---|
| Specialists | `demos/agent-hub/agents/` | Five agents across Contoso, each with a prompt and reference files |
| Storage specialist | `demos/fleet-triage/` | The fleet-triage agent, registered in the hub by its own seeder |
| Orchestrator prompt | `demos/agent-hub/orchestrator/AGENTS.md` | Split, pick an agent, delegate, answer |
| Remote subagents | `custom_demo/runtime/remote_subagents.py` | A2A agents as per-run subagents, background tasks and wake-up |
| Seeder | `scripts/seed_agent_hub.py` | Pushes the Hub repos, creates the specialists, and gives the orchestrator every registered agent as a remote agent |

The registry is the Agent Server itself. Any assistant whose metadata carries
`registry: agent-hub` is listed, and its A2A agent card
(`/.well-known/agent-card.json?assistant_id=...`) supplies the description the orchestrator
chooses on.
Adding an agent to the hub is creating an assistant with that tag and a good
description; nothing in the orchestrator changes.

| Agent | Owns |
|---|---|
| `storage_fleet_triage` | Array performance and health: latency, firmware regressions, failing drives |
| `it_service_desk` | Laptops, VPN, passwords, SSO, software, IT tickets |
| `deal_desk` | Flex and hardware pricing, discounts, approvals, quotes |
| `people_policy` | PTO, leave, travel and expenses, hybrid work |
| `order_tracker` | Customer orders, shipments, backorders, part supply |
| `product_security` | Security bulletins and CVEs for storage firmware |

## Running it

```bash
python -m fleet_mcp_server                  # Contoso Fleet Ops on :8766, for the storage agent
./run.sh --n-jobs-per-worker 12                # Agent Server on :2024 and the SPA on :3000
uv run python scripts/seed_fleet_triage.py         # storage specialist (registers itself)
uv run python scripts/seed_agent_hub.py          # the other five specialists and the orchestrator
```

`--n-jobs-per-worker` matters: `langgraph dev` otherwise runs one job at a time, so a
burst investigates serially and an orchestrator waiting on an A2A delegation deadlocks
against it.

In the SPA, pick **Contoso Agent Hub**. Prompts that show the routing:

- "Is firmware 6.1.2.100 affected by any security bulletins, and what is the status of
  Northwind Health's SSD order?" Two agents, delegated in parallel.
- "The S6X drive in bay 9 on CZ26F5LL3N is throwing media errors. Is that a known issue,
  and how fast can Fourth Coffee get the replacement (order SO-771560)?" Storage triage and order tracking, with an
  answer that connects the failing drive batch to the backorder.
- "What's the cafeteria menu today?" No agent's description covers it, and the hub says so
  instead of guessing.
