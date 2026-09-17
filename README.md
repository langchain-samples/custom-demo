# Custom Demo Agent

A customizable deep agent for customer demos: analyze data, build dashboards or HTML assets,
and use connected tools. Each customer gets their own branding, instructions and sample questions.

[Watch the walkthrough](https://www.loom.com/share/d5ce4bb5a2b5485baef75d0a1d84f825).

If you're a LangChain employee, you can access a hosted version [here](https://custom-demo-langchain.vercel.app/).

## Prerequisites

- **uv** and **Python 3.13** (uv installs the pinned Python version).
- **Node 22.12+ and npm** for the frontend.
- Set `LANGSMITH_API_KEY` and `ANTHROPIC_API_KEY` in the root `.env` (for the default model).

## Run locally

```bash
uv sync --group dev
cp .env.example .env                   # fill in LangSmith and model-provider keys
uv run python scripts/preflight.py     # checks connectivity; makes real API calls
./run.sh                              # backend :2024, frontend :3000
```

Open <http://127.0.0.1:3000>. In **Settings**, choose a workspace, then **+ New** to enter a
use case. Setup creates the branded assistant, sample files, skills and questions.

**Setup time:** A new use case takes about 30 seconds to setup. 
Sandbox prewarming runs in the background; 
the first turn can be slow while the VM and analysis packages start.

> Optionally enable `demo traffic` to populate 200 sample traces for Monitoring, Insights and Engine.

## What this demos

| Platform | Features |
|---|---|
| **Deep Agents** | Skills, sandbox/code execution, dynamic subagents, MCP tools and Apps, human approval, generative UI (streamed dashboards and HTML assets) |
| **LangSmith** | Context Hub prompts/skills, tracing, Monitoring, Insights, **Engine** (issue detection), Evals and live prompt fixes |


## Architecture


A basic **`create_deep_agent` with custom tools and middleware**, plus a React frontend:

- **Setup** resolves the customer scenario and prepares data, skills, prompts and evals.
- **Runtime** applies the assistant's model/tools, reads its prompt fresh and runs the agent.
- **Sandbox** owns working files; Context Hub stores prompts and skills.
- **Frontend** streams tool activity, dashboards and HTML assets from the same conversation.

### Assistants: 
**A use-case-specific configuration of the shared demo agent. **

Its `context` controls execution; `metadata` holds branding and quick actions.

```jsonc
// Research: dashboards, web search and a connected operations system.
{
  "enabled_tools": ["push_widget", "web_search"],
  "mcp_servers": [{ "id": "ops", "label": "Operations", "url": "https://your-server.example/mcp" }]
}
```

## Additional ways to run

| Mode | How |
|---|---|
| **Local** | `./run.sh`; override ports with `PORT` and `SPA_PORT` |
| **Deployed** | The configured LangSmith GitHub integration deploys the backend; Vercel builds the frontend. Configure `VITE_LG_URL` and `VITE_LG_API_KEY` in Vercel; provider keys belong on the backend deployment. |
| **Local frontend, deployed backend** | Set `VITE_LG_URL` and `VITE_LG_API_KEY` in `frontend/.env.local`, then `npm --prefix frontend run dev -- --port 3000`. An existing `lgUrl` localStorage override takes precedence. |
| **Voice** | Click the composer microphone. Requires `GEMINI_API_KEY` on the backend; no per-assistant enable flag. |
| **Connected systems / MCP Apps** | Add a server in **Settings → MCP servers**, then press **Test**. See below. |


Implementation details: [AGENTS.md](AGENTS.md). Development and checks: [CLAUDE.md](CLAUDE.md).
More demos: [voice](docs/voice-mode.md), [MCP Apps](docs/mcp-apps-with-deep-agents.html),
[release evals](evals/README.md) (these score the planted bug firing, opposite to presenter evals).

