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
- tools (through suppling your own mcp server urls)
- subagents (through suplying your own A2A agent urls)
- UI "skin" (logo/name/color)

If you're a LangChain employee, you can access a hosted version [here](https://custom-demos-599b02fd350b553b832acd74983fa55a.us.langgraph.app/ui/). It requires a password. Ask @josiahcoad for it.
Otherwise you can easily run locally and just supply your own LangSmith Api Key.

## Run locally

`cp .env.example .env`

Set `LANGSMITH_API_KEY` and `ANTHROPIC_API_KEY` in the root `.env`.

```bash
uv sync --group dev
uv run python scripts/preflight.py     # checks connectivity; makes real API calls
./run.sh                              # backend :2024, frontend :3000
```

Open <http://127.0.0.1:3000>.

## Agent on Managed Deep Agents (optional)

The agent can run on [Managed Deep Agents](https://docs.langchain.com/langsmith/python/managed-deep-agents-overview)
while setup, the UI and the custom routes stay on the LangSmith deployment.

```bash
uv run python scripts/agent_auth_key.py >> .env   # signing key, shared by both deployments
mda deploy .                                       # prints the MDA deployment URL
```

Then set `AGENT_URL`, `AGENT_JWT_ISSUER` and `MDA_CORS_ORIGINS` in `.env` (see
`.env.example`), redeploy the LangSmith deployment, and copy existing assistants across:

```bash
uv run python scripts/migrate_assistants_to_mda.py --from-url https://<langsmith-deployment>
```

What moves and what stays: [AGENTS.md](AGENTS.md), "The agent on Managed Deep Agents".

## Creating your agent in the UI
When you open the UI, you'll be met with a setup modal.

All you need to provide to create a new agent is:
- Company Name (optional)
- Company Website (optional... for setting up the UI Skin)
- Use Case (optional; can use the brand website to infer a use case)

A new use-case/agent takes about 40 seconds to setup. Behind the scenes, we are... 
- pulling the brand details
- writing the system prompt
- provisioning a sandbox
- creating some dummy files in the sandbox
- creating some skills
- creating some "quick prompts" for you to click to demo the agent

> Tip: Optionally enable `demo traffic` to populate 200 sample traces for Monitoring, Insights and Engine.

You can further tweak your agent in the side-panel.

## Architecture

A `deepagent`, using `assistants` to store per-use-case configuration, plus a React frontend:

- **Setup** resolves the customer scenario and prepares data, skills, prompts and evals.
- **Runtime** applies the assistant's model/tools, reads its prompt fresh and runs the agent.
- **Sandbox** owns working files; Context Hub stores prompts and skills.
- **Frontend** streams tool activity, dashboards and HTML assets from the same conversation.


Implementation details: [AGENTS.md](AGENTS.md). Development and checks: [CLAUDE.md](CLAUDE.md).
More demos: [voice](docs/voice-mode.md), [MCP Apps](docs/mcp-apps-with-deep-agents.html),
[release evals](evals/README.md) (these score the planted bug firing, opposite to presenter evals).
