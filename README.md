# Custom Demo Agent

### Summary
This repo is a chat agent built on `deepagents`. It showcases every feature of DeepAgents (and many features of LangSmith). 
- DeepAgents: Subagents, Skills, Code-execution, Generative UI, file read/write, voice, ...
- LangSmith: Context Hub, deployments, evals, sandboxes, monitoring, Engine, ...

If you're wanting to build your own chat agent, this should hopefully serve as a useful reference for your coding agent to see how each feature is used.

### Customizable!
Customize agents for your use case. Customize the:
- system prompt
- skills
- tools (through suppling your own mcp server url's)
- subagents (through suplying your own A2A agent urls)
- UI "skin" (logo/name/color)

If you're a LangChain employee, you can access a hosted version [here](https://custom-demos-599b02fd350b553b832acd74983fa55a.us.langgraph.app/ui/). It requires a password. Ask @josiahcoad for it.
Otherwise you can easily run locally and just supply your own LangSmith Api Key.

## Run locally

### Prerequisites

- **uv** and **Python 3.13** (uv installs the pinned Python version).
- **Node 22.12+ and npm** for the frontend.
- Set `LANGSMITH_API_KEY` and `ANTHROPIC_API_KEY` in the root `.env` (for the default model).


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


## Architecture


A basic **`create_deep_agent` with custom tools and middleware**, plus a React frontend:

- **Setup** resolves the customer scenario and prepares data, skills, prompts and evals.
- **Runtime** applies the assistant's model/tools, reads its prompt fresh and runs the agent.
- **Sandbox** owns working files; Context Hub stores prompts and skills.
- **Frontend** streams tool activity, dashboards and HTML assets from the same conversation.


Implementation details: [AGENTS.md](AGENTS.md). Development and checks: [CLAUDE.md](CLAUDE.md).
More demos: [voice](docs/voice-mode.md), [MCP Apps](docs/mcp-apps-with-deep-agents.html),
[release evals](evals/README.md) (these score the planted bug firing, opposite to presenter evals).
