# Custom Demo Agent

A **deep agent** that answers a question by building a live dashboard: it reads the data it
has, emits validated widget specs (KPI cards, charts, tables, key findings), and a React SPA
renders each one the moment its tool call finishes streaming.

It is a **customer-demo platform**. One shared graph, one parameterized frontend, and a
per-customer **assistant** carrying that customer's branding, prompt, data and capabilities.
Setting up a new demo is a form, not a fork.

📹 **[Watch the walkthrough](https://www.loom.com/share/d5ce4bb5a2b5485baef75d0a1d84f825)** (Loom)

## Prerequisites

None of which this repo can install for you:

| | | |
| :-- | :-- | :-- |
| **uv** | required | `curl -LsSf https://astral.sh/uv/install.sh \| sh` - also provisions Python |
| **Python 3.13 exactly** | required | uv installs it from `.python-version`; `requires-python` bars 3.14 |
| **Node 20+ / npm** | for the UI | the SPA is a Vite app. The agent and its evals run without it |
| **LangSmith key** | required | tracing, Context Hub, the sandbox, and the demo evals |
| **A model provider key** | required | Anthropic by default; any `init_chat_model` provider works |

**3.13, not "3.13 or newer".** 3.13 is the newest Python the LangGraph deployment base image
builds, so CI, `.python-version` and `requires-python = ">=3.13,<3.14"` all hold the line there
- otherwise a 3.14-only behaviour passes CI and fails the deploy. If you last synced this repo
on 3.14, your `.venv` is now the wrong interpreter and `uv sync` will refuse it:

```bash
rm -rf .venv && uv sync --group dev      # rebuilds on 3.13 (uv downloads it if needed)
```

## Quickstart

```bash
uv sync --group dev                          # + langgraph-cli[inmem], langgraph-sdk

cp .env.example .env                         # then edit: LANGSMITH_API_KEY + a model key
uv run python scripts/preflight.py           # names what is missing and how to fix it

uv run ./run.sh                              # Agent Server :2024 + the SPA :3000
```

Then open <http://127.0.0.1:3000>, and in ⚙️: pick a **Workspace**, hit **+ New** to set up a
customer assistant, and ask it something.

Run `preflight.py` before anything else. It makes one cheap model call and one LangSmith
round-trip, then prints either `ALL CHECKS PASSED` or the specific fix. Most setup problems show
up there rather than as a stack trace ten minutes into a demo.

## Ways to run it

| Mode | How | Use it when |
| :-- | :-- | :-- |
| **Local** | `uv run ./run.sh` | developing. Agent Server on `:2024`, SPA on `:3000`; `PORT`/`SPA_PORT` override |
| **Deployed** | push to `main` | the LangSmith deployment rebuilds itself (`build_on_push`) and Vercel rebuilds the SPA. The SPA finds the backend through `VITE_LG_URL` + `VITE_LG_API_KEY`, baked in at build time from the Vercel project's env |
| **Local SPA, deployed backend** | set `lgUrl` in localStorage, or ⚙️ | reviewing a UI change against real assistants. Note the reverse does not work: a deployed SPA cannot reach `http://127.0.0.1:2024`, because browsers block that from an https page |
| **With an MCP server** | ⚙️ → MCP servers | giving an assistant tools this repo does not own. See [Connecting an MCP server](#connecting-an-mcp-server) |
| **Voice** | the mic in the composer | a spoken demo. Needs `GEMINI_API_KEY` and the assistant's voice flag. See [docs/voice-mode.md](docs/voice-mode.md) |

**Warm the sandbox before demoing: a cold first turn can take three minutes.** The agent reads
files for everything, and those files live in a per-assistant VM. `+ New` pre-warms it in the
background, but ask a question before that finishes and the turn blocks behind it, showing
nothing but a spinner. Measured on a real first turn: 210s, of which the VM boot is capped at
25s (`_SANDBOX_WAIT_SECONDS`) and the rest is the seed script installing pandas, numpy,
statsmodels and scikit-learn into the VM. So create the assistant, ask one throwaway question,
and wait for it to answer before you present. The same wait returns if the VM is left idle for
an hour and gets reaped. With `SANDBOX_ENABLED=0` there is no VM at all, and the agent will say
plainly that it has no data source.

### If something doesn't work

| Symptom | Cause |
| :-- | :-- |
| "I have no data source", or no figures | `SANDBOX_ENABLED=0`, or the VM is still booting. Ask again in 30s |
| `ANTHROPIC_API_KEY is not set` on a non-Anthropic setup | `AGENT_MODEL` still defaults to Anthropic. Set it to your `provider:model` and that provider's key |
| Azure calls 404 | `AZURE_OPENAI_ENDPOINT` includes `/openai/deployments/...`. It must stop before that; the client appends it |
| The model rejects `temperature` | Reasoning-tuned models allow only their own default. Set `MODEL_TEMPERATURE=` (empty) to omit it |
| Editing the prompt changes nothing | You edited a different assistant's repo. Each one has its own, named `<slug>-agent` |
| Model calls go somewhere unexpected | `ANTHROPIC_BASE_URL` is set in your shell and redirects everything. Preflight warns about this |

**Running this with a group.** Each `+ New` assistant gets its own Context Hub agent repo
(`<slug>-agent`) and its own eval dataset, so people do not overwrite each other **as long as
they pick distinct customer names**.

## Is the answer grounded?

The demo shows an agent inventing a figure, so a presenter has to be able to tell a real answer
from a fabricated one, live. There is one rule:

> **Every figure comes from a file. If no tool call read one, the model made it up.**

Open the trace (**Traces** in the header, or the link under any answer) or the graph inspector,
and look for an `execute` or a `read_file` before the number. Then check the file yourself: the
files button in the header browses the agent's VM (rooted at `/workspace`, with the seeded
files under `/workspace/data`), and the rows you see are the rows the agent saw. That is the whole verification, and it works because there is no second source of
truth.

## The planted bug, and fixing it live

Each assistant is created with a **failure mode**. With `hallucination`, the system prompt tells
the agent to answer confidently over gaps, and setup plants one: a topic the seeded files
genuinely do not cover, plus a quick action that asks about it. The arc:

1. Two grounded questions get real, checkable answers.
2. The third, the gap probe, gets a confident fabrication.
3. Open the assistant's prompt in **LangSmith Context Hub** (its `<slug>-agent` repo,
   `AGENTS.md`), delete the fabricate-over-gaps clause, save. The next question is honest,
   with **no restart**: the prompt is pulled per turn.
4. **Evals** in the header scores the arc against that assistant's own dataset. 2/3 before the
   fix, 3/3 after.

The gap is a real absence rather than an instruction to withhold, which is what makes step 2
reliable: the agent has nowhere to read the figure from, so stating it is a genuine
hallucination.

## Assistants are the demo

An [assistant](https://docs.langchain.com/langsmith/assistants) is a stored configuration of
the one graph: switch by `assistant_id`, no redeploy. `+ New` fetches the customer's logo,
brand palette and typefaces, writes a templated system prompt to their Context Hub, seeds
their VM with plausible files, and creates an eval dataset.

Everything behavioural lives in the assistant's `context`:

```jsonc
// Minimal: the prompt is the AGENTS.md of a Context Hub agent repo.
{ "agent_repo": "acme-agent", "ls_workspace": "<uuid>", "customer": "Acme" }
```

```jsonc
// A support assistant: no dashboard, one extra capability.
{ "agent_repo": "acme-agent", "ls_workspace": "<uuid>",
  "enabled_tools": ["draft_email"],          // [] means every optional tool OFF
  "customer": "Acme", "industry": "Retail" }
```

```jsonc
// Wired to a customer's own system over MCP, with seeded files of its own.
{ "agent_repo": "acme-agent", "ls_workspace": "<uuid>",
  "enabled_tools": ["push_widget", "web_search"],
  "mcp_servers": [{ "id": "acme", "label": "Acme Ops", "url": "https://x.ngrok.app/mcp" }],
  "sandbox_seed": [{ "name": "orders.csv", "kind": "csv", "description": "12 months of orders" }] }
```

```jsonc
// On a different model. ⚙️ → Agent config → Model offers Claude Sonnet 5 (the
// default, which sends no `model` at all) and NVIDIA Nemotron 3 Ultra.
{ "agent_repo": "acme-agent", "customer": "Acme",
  "model": "langsmith:fireworks/accounts/fireworks/models/nemotron-3-ultra-nvfp4" }
```

`model` takes any `init_chat_model` id, so `provider:model` picks the provider. The
`langsmith:` prefix routes through the LangSmith gateway, which needs the deployment's
LangSmith key to carry `gateway:invoke` and the workspace to hold that provider's secret.
See "Non-Anthropic model providers" in `.env.example` to change the default for every
assistant instead.

Branding (`display_name`, `logo`, `accent`, fonts, quick actions) lives in the assistant's
`metadata` instead, and the SPA writes edits straight back, so it is reusable across people.

## Tools

Two independent sources.

**A selectable catalogue** (`runtime/tools/registry.py`), which each assistant picks from in the
create form or ⚙️ → Tools:

| tool | what it does |
|---|---|
| `push_widget` | appends one validated visualization to the dashboard (on by default) |
| `draft_email` | composes an email, then **pauses for you to edit and approve it** |
| `ask_user` | **pauses** to ask a multiple-choice question, then continues with your answer |
| `web_search` | **real** web search via Tavily. Without `TAVILY_API_KEY` it errors rather than inventing sources |

**deepagents built-ins**, always present and never filtered: the filesystem set
(`ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`, `delete`), `task`, and `execute`
when the sandbox is up. **The filesystem tools are how the agent gets data.** There is no retrieval tool.

Changing an assistant's capabilities is a config edit that applies on the next message. Adding a
*new* tool to the catalogue is a code change, and `tests/test_tool_vocabulary.py` fails until the
SPA knows its label and icon.

## Connecting an MCP server

Paste a server's URL into ⚙️ → **MCP servers**, press **Test** to see the tools it advertises,
and they are in play on the next message. The connection is saved on the assistant, so it is
per-customer config like everything else.

**Do I need ngrok?** Only for a *deployed* agent. The agent connects **outbound** to the URL you
give it, so `localhost` inside the deployment's container is the container, not your machine.
Running locally needs no tunnel.

Two demo servers ship in the box, one per business:

```bash
./scripts/run_mcp_server.sh --wealth --tunnel   # Meridian Wealth (advisory)
./scripts/run_mcp_server.sh --tunnel            # Fieldlink Logistics
./scripts/run_mcp_server.sh --wealth            # local only: http://127.0.0.1:8765/mcp
```

Paste the printed URL **including the `/mcp` path**. A new hostname is issued per run, so
re-paste after a restart, and put a bearer token on anything you leave up: a tunnel is public.

Both are written against the modern stateless MCP spec, so between them they exercise a
cacheable tool list, **elicitation** (the server stops mid-call to ask, and the SPA builds a form
from the schema it asked for) and **MCP Apps** (the server ships its own HTML and the SPA renders
it in a sandboxed iframe: allocation sliders, a goal projection, an order ticket with
hold-to-confirm, a signature pad).

For how that works end to end, and the four failure modes that do not announce themselves, see
[docs/mcp-apps-with-deep-agents.html](docs/mcp-apps-with-deep-agents.html).

## Tests

```bash
uv run pytest dashboard_agent/tests evals -q          # the whole suite, ~4s
uv run ruff check dashboard_agent scripts evals        # + ruff format --check, ty check
node dashboard_agent/tests/signature_app_test.js       # the MCP App's postMessage contract
cd frontend && npx tsc -b && npx oxlint && npm test && npm run build
```

The live-LLM tests skip themselves without `ANTHROPIC_API_KEY`, which CI leaves unset on purpose
so nothing there makes a real API call. `test_hallucination_bug.py` also needs the sandbox, for
the reason in the section above.

## Architecture

A stock `create_deep_agent` plus custom tools and a few middlewares. Nothing exotic.

```
dashboard_agent/
  config.py       env, credentials, model ids. The hub: everything imports it, it imports nothing
  core/           primitives with no dependencies of their own (ctx)
  runtime/        what runs on a chat turn: agent, prompt, tools/, mcp_servers, widgets, mocking
  provisioning/   building a demo: setup, evals, traffic, tags. Depends on runtime, never the reverse
  voice/          Live API token minting + conversation tracing
  graph.py        the deployed graph          } named by langgraph.json,
  setup_graph.py  the assistant_setup graph   } so these four stay
  auth.py         shared-secret auth          } at the package root
  webapp.py       extra Starlette routes      }
frontend/         React 19 + Vite + Tailwind SPA (the real UI)
mcp_demo_server/  two FastMCP servers to connect the agent to
evals/            repo-level evals, run before a release (score 1 = the planted bug fired)
```

The middlewares are the interesting part. `ConfigurableModel` swaps the LLM from
`context.model`; `_hub_system_prompt` pulls the prompt fresh per question, which is what lets you
fix the bug live; `McpTools` binds remote tools per run; `ToolSelection` filters the catalogue to
what this assistant enabled; `RubricMiddleware` grades a turn against a `/goal`. They run in a
fixed build order, and that order is load-bearing: the middleware table in
[AGENTS.md](AGENTS.md) is the one place it is written down.

**Widget streaming.** The SPA hits `/threads/{id}/runs/stream` directly and reconstructs each
widget from the partial `push_widget` args, flushing one when the next begins. That is why the
dashboard assembles a card at a time instead of appearing at once. Every widget is re-validated
against its Pydantic schema server-side.

For anything deeper - middleware ordering rules, the sandbox lifecycle, the eval polarity, the
MCP protocol traps - read [AGENTS.md](AGENTS.md), the orientation doc for working *on* this
rather than *with* it.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | from `.env` | **required** - agent model |
| `LANGSMITH_API_KEY` | from `.env` | **required** - Context Hub, sandbox, feedback |
| `LS_CROSS_WORKSPACE_KEY` | falls back to `LANGSMITH_API_KEY` | org-scoped key, needed to route traces and prompts to *another* workspace |
| `LANGSMITH_ENDPOINT` | `https://api.smith.langchain.com` | LangSmith API base URL |
| `WORKSPACE_ID` | the API key's workspace | scopes the LangSmith client when your key spans several workspaces. Unset is correct for a single-workspace key |
| `PROJECT_NAME` | `custom-demo` | fallback tracing project. An assistant traces to its customer name, or to `context.ls_project` when set. Was `dashboard-agent` until the rename below; a project by that name may still hold older runs |
| `AGENT_MODEL` | `claude-sonnet-5` | agent model |
| `MODEL_TEMPERATURE` | each call site's own | empty omits `temperature` entirely, for models that reject any but their default |
| `JUDGE_MODEL` | a Haiku id | demo-eval judge; pinned separately so swapping the agent model does not move it |
| `SIMULATED_MODEL` | a Haiku id | fast model behind the simulated tools |
| `SANDBOX_ENABLED` | `1` | exactly `0` disables the code-execution VM, which leaves the agent with no data source |
| `TAVILY_API_KEY` | unset | required by `web_search` |
| `GEMINI_API_KEY` | unset | enables voice mode |
| `BRANDFETCH_API_KEY` | unset | accurate brand palette and typefaces at setup; falls back to an LLM guess |
| `LOGODEV_TOKEN` | bundled key | Logo.dev key for customer logos |
| `LANGGRAPH_URL` | `http://127.0.0.1:2024` | Agent Server the `scripts/` helpers talk to |

The model variables were once `DASHBOARD_*` (`DASHBOARD_MODEL`, `DASHBOARD_TEMPERATURE`,
`DASHBOARD_JUDGE_MODEL`, `DASHBOARD_GOAL_MODEL`, `DASHBOARD_GOAL_MAX_ITERATIONS`,
`DASHBOARD_SETUP_MODEL`, `DASHBOARD_SIMULATED_MODEL`, `DASHBOARD_VOICE_MODEL`), from when
building dashboards was the whole of what this did. Every old name is still read as a
fallback, so an existing `.env` or deployment secret keeps working; new ones should use the
names above. `DASHBOARD_DATA_MODEL` is the one exception - it was a fallback for the deleted
synthetic data source and is gone.

The `DA_*` knobs went the same way, because the `DA` was "Dashboard Agent" too:
`DA_SANDBOX` -> `SANDBOX_ENABLED`, `DA_DYNAMIC_SUBAGENTS` -> `DYNAMIC_SUBAGENTS`,
`DA_FILES_ROOT` -> `SANDBOX_FILES_ROOT`, `DA_MCP_TOOLS_TTL` -> `MCP_TOOLS_TTL`,
`DA_MCP_TIMEOUT` -> `MCP_TIMEOUT`. Same fallback, so nothing breaks.

`PROJECT_NAME`'s DEFAULT changed for the same reason, and this one has no fallback:
runs with no `PROJECT_NAME` set used to trace into a LangSmith project called
`dashboard-agent` and now trace into `custom-demo`. If a `dashboard-agent` project
already exists in your workspace, its older runs stay there and new ones land beside
them in the new project; set `PROJECT_NAME=dashboard-agent` to keep appending to the
old one.

## Further reading

- **[AGENTS.md](AGENTS.md)** - how this is built and why, for anyone changing it
- **[docs/agent-development.md](docs/agent-development.md)** - working on the agent itself
- **[docs/voice-mode.md](docs/voice-mode.md)** - the voice spike
- **[docs/mcp-apps-with-deep-agents.html](docs/mcp-apps-with-deep-agents.html)** - MCP Apps, full stack
- **[evals/README.md](evals/README.md)** - the repo-level evals and their inverted polarity
