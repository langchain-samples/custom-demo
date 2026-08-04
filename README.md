# Dashboard Agent — Deep Agent with dynamic dashboard visualizations

A **Deep Agent** that answers a question by **dynamically building a live dashboard**
(KPI cards, charts, tables, key-findings) plus a short written answer. It mirrors the
CopilotKit "shared-state canvas" pattern: the agent emits validated widget specs and the
frontend renders them into a persistent dashboard.

It is a **customer-demo platform**: one shared graph, one parameterized frontend, and a
per-customer **assistant** carrying that customer's branding, prompt, data and capabilities.
Setting up a new demo is a form, not a fork.

The bundled corpus is humanitarian (three demo personas — donor, affected, technical/NGO),
but the synthetic data mode below points the same agent at any industry.

## Architecture

```
dashboard_agent/
  corpus.py     # dummy reports: prose (for grounding) + structured data (for charts)
  rag.py        # dependency-free in-memory TF-IDF retriever  → the datasearch tool
  datasource.py # pluggable backend behind datasearch: humanitarian corpus | synthetic LLM
  tools/        # THE TOOL CATALOGUE: registry.py (selectable capabilities) + core/simulated
  widgets.py    # Pydantic widget schemas (kpi/bar/line/pie/table/text) + validation
  prompt.py     # system + data prompts sourced from LangSmith Prompt Hub (+ fallbacks)
  agent.py      # deep agent: middleware (prompt / model / tool-selection) + Context schema
  graph.py      # Agent Server entrypoint (compiled graph for langgraph.json)
  setup_graph.py + assistant_setup.py  # second graph: prepares a new customer assistant
  webapp.py     # Starlette routes on the deployment: /feedback /tools /projects
                #   /workspaces /hub-prompts
  static/       # LEGACY vanilla-JS SPA, superseded by frontend/
  tests/        # rag, widgets, streaming, tool-registry (fast) + e2e, hallucination (slow)
frontend/       # React + Vite + Tailwind SPA (the real UI)
langgraph.json  # deployment config: both graphs + http.app + CORS
```

**Tools the agent has.** Two independent sources:

- **A selectable catalogue** (`tools/registry.py`) — each assistant chooses which of these it
  exposes, in the create form or Settings → Tools:

  | tool | what it does |
  |---|---|
  | `push_widget` | appends one validated visualization to the dashboard (always on) |
  | `datasearch` | grounded prose + structured data (RAG, or synthetic per the data source) |
  | `list_data_sources` | shows the "connected systems" behind an answer |
  | `draft_email` | composes an email, then **pauses for you to edit and approve it** |
  | `suggest_meeting_times` | proposes slots, then **pauses for you to pick one** |
  | `web_search` | **real** web search via Tavily — looks up external context and cites it |

  Everything except `datasearch` + `push_widget` is off by default. All are LLM-simulated and
  tailored to the customer — no per-customer credentials, nothing to break live — **except
  `web_search`**, which calls the Tavily API and needs `TAVILY_API_KEY` in `.env`. Without the
  key it returns an error rather than inventing sources, so the agent can never cite fake URLs.

- **deepagents built-ins**, always present and never filtered: `write_todos`, the filesystem
  set (`ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`) and `task`.

Changing an assistant's capabilities is a config edit that takes effect on the next message —
no new assistant, no redeploy. Adding a *new* tool to the catalogue is a code change.

**Runs on LangGraph Agent Server.** The compiled deep agent (`graph.py`) is deployed
via `langgraph.json`; the SPA talks directly to the server's `/threads` +
`/runs/stream` (`stream_mode: "messages"`). As the agent streams, each `push_widget`
tool call's args fill in and the frontend renders that widget the moment it looks
complete — so the dashboard builds **one widget at a time**, then the final answer
streams in. Every widget is re-validated against the Pydantic schema server-side.

**Assistants = customer demos.** One graph, many [assistants](https://docs.langchain.com/langsmith/assistants)
(configuration instances) set the `Context` — prompt, dataset, model, enabled tools, customer,
and which LangSmith workspace/project the run's traces land in. Switch by `assistant_id`; no
redeploy. Create one from the ⚙️ panel ("+ New"), which runs the `assistant_setup` graph:
it fetches the customer's logo, brand palette and typefaces, generates persona quick-actions,
and pushes a customer-templated system prompt to that workspace's Prompt Hub.

**Human-in-the-loop.** `draft_email` and `suggest_meeting_times` genuinely pause the run
(`interrupt()`). The draft appears as an editable form — or a slot picker with a date/time
control — and approving resumes the thread with *your* version, which is what the agent then
reports on.

**Per-customer branding.** Brand colours tint the whole shell (panels, borders, chart series),
not just an accent button; text on brand fills gets a contrast-correct colour automatically.
The customer's typeface loads from Google Fonts with a self-hosted fallback when it isn't
available. All of it lives in the assistant's `metadata` and is editable in ⚙️.

**UI:** loads as a centered chat; once the first widget streams in, the chat slides to
a left rail and the dashboard canvas reveals on the right. A **Download PDF** button
exports the canvas via html2pdf. The ⚙️ gear covers workspace, assistant, branding,
typography, agent config and tools.

## Run it

Needs `langgraph-cli[inmem]` (Agent Server) + a LangSmith key.

```bash
# from dashboard-agent/
uv sync --group dev          # dev group includes langgraph-cli[inmem] + langgraph-sdk

# provide your keys (see .env.example)
cp .env.example .env         # then edit: ANTHROPIC_API_KEY + LANGSMITH_API_KEY

# seed the Prompt Hub prompts (once)
uv run python scripts/seed_prompt.py         # system prompt (starts buggy)
uv run python scripts/seed_data_prompt.py    # synthetic data prompt (optional)

# start Agent Server (:2024) + the React SPA (:3000)
uv run ./run.sh

# open the SPA (http://127.0.0.1:3000), then in ⚙️: pick a Workspace, hit "+ New"
# to set up a customer assistant, and ask a question.
```

`scripts/seed_assistants.py` still exists if you want two bare variants
("Humanitarian (bundled corpus)" / "Synthetic — any topic") without the branding flow.

`uv run ./run.sh` syncs the environment, then starts `langgraph dev` plus the
front-end. It bootstraps what's missing: `uv sync --group dev` if there's no venv
or no `langgraph` CLI, and `npm ci` in `frontend/` if `node_modules` is absent. It
warns (but still starts) when `.env` is missing. Plain `./run.sh` works too once
you've synced — the `uv run` prefix just guarantees the env is current first.
Set `PORT` / `SPA_PORT` to override the ports.

## Tests

```bash
# fast (no LLM): rag, widgets, streaming logic, tool registry + selection middleware
uv run pytest dashboard_agent/tests/test_rag.py dashboard_agent/tests/test_widgets.py \
              dashboard_agent/tests/test_streaming_unit.py \
              dashboard_agent/tests/test_tool_registry.py -q

# frontend pure logic (Node — imports the real .ts modules via native type stripping)
node dashboard_agent/tests/branding_test.js    # colour maths, contrast, chart palette
node dashboard_agent/tests/trace_test.js       # trace-project naming

# frontend typecheck / lint / build
cd frontend && npx tsc -b && npx oxlint && npm run build

# real agent e2e across all 3 personas (slow, ~2 min, costs tokens)
uv run pytest dashboard_agent/tests/test_agent_e2e.py -v

# hallucination-bug before/after (slow)
uv run pytest dashboard_agent/tests/test_hallucination_bug.py -v
```

> `dashboard_agent/tests/frontend_test.js` imports the **legacy** `static/app.js`, not the
> React app — it passes regardless of what the SPA does. Treat it as testing dead code.

## The system prompt lives in Prompt Hub (and the planted bug)

The agent's system prompt is **not hardcoded** — it lives in **LangSmith Prompt Hub**
under the name `dashboard-agent-system` and is pulled **fresh on every question** by a
`@dynamic_prompt` middleware (`agent.py` + `prompt.py`). So there is **one** agent and
**no `/fixed` route**: you fix behavior by editing the prompt in the Hub, with no code
change and no restart.

Seed the prompt once (pushes the buggy version — see below):

```bash
python scripts/seed_prompt.py
```

**The planted bug (live-fixable demo):**

- **The bug:** the Hub prompt starts with an `IMPORTANT OVERRIDE` clause telling the
  agent that when a figure is missing from the data it should *guess a plausible number
  and present it confidently as fact* — never admitting the gap. Ask "how many schools
  were rebuilt in Egypt in Q2 2026?" (not in the corpus) and it fabricates a number.
- **The fix — live, no redeploy:** open the prompt in Prompt Hub, delete the
  `IMPORTANT OVERRIDE` clause, and **Commit**. The next question uses the grounded
  prompt and the agent says the figure "is not available in the current reports."

If the Hub is unreachable, the app falls back to the grounded prompt in
`prompt.py` (`FALLBACK_PROMPT`), so it still runs offline (just not live-editable).

## Generalize to any topic (synthetic data)

The `datasearch` tool goes through a pluggable `DataSource` (`datasource.py`):

- **`humanitarian`** (default) — the bundled corpus, via real in-memory TF-IDF.
- **`synthetic`** — a fast LLM *stands in* for the backend and invents plausible data per
  call, anchored to today's date. The **topic and the planted gap** come from the assistant's
  `data_gap` / `customer` context, or from a Prompt Hub prompt (`dashboard-agent-data`), so
  you can point the demo at any domain and control what data exists — live, no redeploy.

The data source is chosen per **assistant** via its `Context` (`dataset`, `data_model`,
`data_gap`, `data_prompt` / `data_prompt_name`). The "+ New" flow sets `synthetic`
automatically when you enable the hallucination demo. For a purely local (non-deployment)
run, `DASHBOARD_DATASET=synthetic` is the env fallback the tools read when no context is set.

The grounding story is preserved: the data prompt withholds the trap figure (e.g.
"schools rebuilt"), the tool returns nothing, and the main agent's buggy prompt
fabricates over the gap — exactly the same catch-and-fix demo, now domain-agnostic.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | (from `.env`) | **required** — agent model |
| `LANGSMITH_API_KEY` | (from `.env`) | **required** — Prompt Hub pulls + feedback |
| `LS_CROSS_WORKSPACE_KEY` | (falls back to `LANGSMITH_API_KEY`) | org-scoped key — needed to route traces/prompts to *another* workspace |
| `LANGSMITH_ENDPOINT` | `https://api.smith.langchain.com` | LangSmith API base URL |
| `WORKSPACE_ID` | (API key's workspace) | default LangSmith workspace/tenant |
| `PROJECT_NAME` | `dashboard-agent` | fallback tracing project (assistants use `<client>-corebot-demo`) |
| `DASHBOARD_MODEL` | `claude-sonnet-5` | agent model |
| `DASHBOARD_PROMPT` | `dashboard-agent-system` | Prompt Hub name to pull the system prompt from |
| `DASHBOARD_DATASET` | `humanitarian` | `synthetic` = live-LLM data backend for any topic |
| `DASHBOARD_DATA_MODEL` | `anthropic:claude-haiku-4-5-20251001` | fast model for synthetic data + the simulated tools (`init_chat_model` id) |
| `DASHBOARD_DATA_PROMPT` | `dashboard-agent-data` | Prompt Hub name for the synthetic data prompt |
| `BRANDFETCH_API_KEY` | (unset) | optional — accurate brand palette + typefaces at setup; falls back to an LLM guess |
| `LOGODEV_TOKEN` | (bundled publishable key) | optional — Logo.dev key for customer logos |
| `LANGGRAPH_URL` | `http://127.0.0.1:2024` | Agent Server the `scripts/` helpers talk to |
