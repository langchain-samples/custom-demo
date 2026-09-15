# Custom Demo Agent

A customizable deep agent for customer demos: analyze data, build dashboards or HTML assets,
and use connected tools. Each customer gets their own branding, instructions and sample questions.

[Watch the walkthrough](https://www.loom.com/share/d5ce4bb5a2b5485baef75d0a1d84f825).

## Prerequisites

- **uv** and **Python 3.13** (uv installs the pinned Python version).
- **Node 22.12+ and npm** for the frontend.
- A **LangSmith key** and a **model-provider key** (Anthropic by default).
  See `.env.example` for configuration, including other providers and cross-workspace access.

## Run locally

```bash
uv sync --group dev
cp .env.example .env                   # fill in LangSmith and model-provider keys
uv run python scripts/preflight.py     # checks connectivity; makes real API calls
./run.sh                              # backend :2024, frontend :3000
```

Open <http://127.0.0.1:3000>. In **Settings**, choose a workspace, then **+ New** to enter a
customer and use case. Setup creates the branded assistant, sample files, skills and questions.

**Before presenting:** ask a sample question and wait for a complete answer. Sandbox prewarming
runs in the background; the first turn can be slow while the VM and analysis packages start.
Use distinct customer names for independent demos in one workspace: same-customer assistants
can share prompt/skill repositories and eval datasets.

## What this demos

| Platform | Features |
|---|---|
| **Deep Agents** | Skills, sandbox/code execution, dynamic subagents, MCP tools and Apps, human approval, generative UI (streamed dashboards and HTML assets) |
| **LangSmith** | Context Hub prompts/skills, tracing, Monitoring, Insights, **Engine** (issue detection), Evals and live prompt fixes |

Dynamic subagents require `DYNAMIC_SUBAGENTS=1`; MCP requires a connected server.
Optional demo traffic populates Monitoring and attempts to start Insights and Engine, subject to
workspace permissions and availability.

## Ways to run and demo

| Mode | How |
|---|---|
| **Local** | `./run.sh`; override ports with `PORT` and `SPA_PORT` |
| **Deployed** | The configured LangSmith GitHub integration deploys the backend; Vercel builds the frontend. Configure `VITE_LG_URL` and `VITE_LG_API_KEY` in Vercel; provider keys belong on the backend deployment. |
| **Local frontend, deployed backend** | Set `VITE_LG_URL` and `VITE_LG_API_KEY` in `frontend/.env.local`, then `npm --prefix frontend run dev -- --port 3000`. An existing `lgUrl` localStorage override takes precedence. |
| **Voice** | Click the composer microphone. Requires `GEMINI_API_KEY` on the backend; no per-assistant enable flag. |
| **Connected systems / MCP Apps** | Add a server in **Settings → MCP servers**, then press **Test**. See below. |

For frontend-only mode, run `npm --prefix frontend ci` first on a fresh checkout.

For a local MCP demo server:

```bash
./scripts/run_mcp_server.sh --tunnel           # Fieldlink Logistics
./scripts/run_mcp_server.sh --wealth --tunnel  # Meridian Wealth
```

Paste the printed URL **including `/mcp`**. Omit `--tunnel` when the agent also runs locally;
a deployed agent cannot reach your laptop's `localhost`. Protect publicly exposed servers.

Try a sample question, request a dashboard for metrics, or ask for an **HTML report/one-pager**
when a document fits better. **Demo traffic → Generate** populates LangSmith monitoring with
synthetic runs, not a source-of-truth ticket database; it is optional and incurs real usage.

### Show a failure, then fix it

Create an assistant with the `hallucination` failure mode. Setup leaves a gap in the sample data
and includes a question that probes it:

1. Run the grounded sample questions, then the gap question; inspect the answers and sources.
2. In **LangSmith Context Hub**, open the saved assistant's `<slug>-agent` repo and edit
   `AGENTS.md`: remove the **fabricate-over-gaps clause** and save.
3. Ask again and rerun **Evals**. Prompt edits apply without a restart.

The intended full-scenario result is **2/3 passing before, 3/3 after**, not a guaranteed score.

## How do I know an answer wasn't hallucinated?

**A successful run is not proof of grounding. Inspect the actual tool results.**

1. Open the answer's trace and find the relevant `read_file`, `execute`, MCP or search result.
   Check the returned record and any filtering/join/calculation, not just the tool's success status.
2. For seeded data, open **Files → `/workspace/data`** on the **same assistant**. Match the
   exact file, record ID and fields against the answer. A customer-name mismatch is unsupported
   unless another retrieved source or an explicit transformation explains it.
3. If the rows differ from the trace, check the assistant, file path and whether the file changed.
   Files shows the VM's **current** contents, not a historical snapshot of that run.
4. For MCP or web answers, check the returned records or cited pages instead; those sources
   need not appear in the local data folder. A read in an earlier conversation turn may also matter.

The seeded files are **synthetic demo data**. They let you verify whether the agent used the
provided records faithfully; they do not establish facts about the real customer.

## Tools

| Tool | What it does |
|---|---|
| `push_widget` | Streams KPI cards, charts, tables and findings; on by default, switchable off |
| `draft_email` | Drafts an email for editing and approval; does not send it |
| `ask_user` | Pauses for a multiple-choice answer; always available |
| `web_search` | Real Tavily results; requires `TAVILY_API_KEY` |
| Filesystem + `execute` | Read/write files, run analysis and produce HTML assets; execution requires a sandbox |
| `task` / MCP tools | Delegate work or call configured external systems |

Choose optional tools in Settings. `SANDBOX_ENABLED=0` disables code execution;
`DYNAMIC_SUBAGENTS=1` enables QuickJS orchestration of named subagents.

## Assistants and configuration

An **assistant** is a customer-specific configuration of the shared demo agent: switch assistants,
not applications. Its `context` controls execution; `metadata` holds branding and quick actions.

Example **context edits** for an existing setup-generated assistant (merge these fields; retain
its workspace, prompt/skills repos, `sandbox_key` and `sandbox_seed`):

```jsonc
// Support: text/HTML answers and email drafts, without dashboard widgets.
{ "enabled_tools": ["draft_email"] }
```

```jsonc
// Research: dashboards, web search and a connected operations system.
{
  "enabled_tools": ["push_widget", "web_search"],
  "mcp_servers": [{ "id": "ops", "label": "Operations", "url": "https://your-server.example/mcp" }]
}
```

Set `model` to a supported `provider:model` identifier to override the deployment default.
At runtime, `enabled_tools: []` disables optional tools; `ask_user` remains. Setup unions its
initial picks with defaults, so use Settings after creation to turn optional tools off.

**Prompt choice versus prompt editing:** the Settings repo dropdown is a temporary chat override;
evals use saved assistant configuration. Edit that saved repo's `AGENTS.md` in Context Hub to
change the instructions used by both.

## Architecture

A basic **`create_deep_agent` with custom tools and middleware**, plus a React frontend:

- **Setup** resolves the customer scenario and prepares data, skills, prompts and evals.
- **Runtime** applies the assistant's model/tools, reads its prompt fresh and runs the agent.
- **Sandbox** owns working files; Context Hub stores prompts and skills.
- **Frontend** streams tool activity, dashboards and HTML assets from the same conversation.

Implementation details: [AGENTS.md](AGENTS.md). Development and checks: [CLAUDE.md](CLAUDE.md).
More demos: [voice](docs/voice-mode.md), [MCP Apps](docs/mcp-apps-with-deep-agents.html),
[release evals](evals/README.md) (these score the planted bug firing, opposite to presenter evals).

This is a shared-token demo system, not tenant-isolated production hosting. Use non-sensitive
data and restrict deployment access.
