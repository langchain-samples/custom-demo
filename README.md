# Dashboard Agent — Deep Agent with dynamic dashboard visualizations

A **Deep Agent** that answers questions about humanitarian operations by
**dynamically building a live dashboard** (KPI cards, charts, tables, key-findings)
from retrieved data — plus a short written answer. It mirrors the CopilotKit
"shared-state canvas" pattern: the agent emits validated widget specs and the
frontend renders them into a persistent dashboard.

Built for the three demo personas:

| Persona | Example question |
|---|---|
| **Donor** | "What is the impact of humanitarian aid in Egypt over the last quarter?" |
| **Affected / vulnerable** | "What are the available resources for displaced families in Iran?" |
| **Technical / NGO** | "Latest data on water scarcity and sanitation needs in Canada?" |

## Architecture

```
dashboard_agent/
  corpus.py     # dummy reports: prose (for grounding) + structured data (for charts)
  rag.py        # dependency-free in-memory TF-IDF retriever  → the datasearch tool
  database.py   # in-memory SQLite seeded from corpus        → the query_sql tool
  datasource.py # pluggable backend behind the tools: humanitarian corpus | synthetic LLM
  widgets.py    # Pydantic widget schemas (kpi/bar/line/pie/table/text) + validation
  prompt.py     # system + data prompts sourced from LangSmith Prompt Hub (+ fallbacks)
  agent.py      # deep agent: tools + dynamic prompt middleware + Context schema
  graph.py      # Agent Server entrypoint (compiled graph for langgraph.json)
  webapp.py     # tiny Starlette app mounted on the deployment: POST /feedback
  static/       # index.html + app.js + config.js (SPA → Agent Server /threads + /runs/stream)
  tests/        # rag, widgets, SQL, streaming-logic, real-agent e2e, Node, hallucination
langgraph.json  # deployment config: graph + http.app + CORS
```

**Tools the agent has:**
- `datasearch` — grounded prose + structured data (RAG, or synthetic per the data source).
- `query_sql` — read-only SQL SELECT (against SQLite, or passed to the synthetic data LLM).
- `push_widget` — appends one validated visualization to the dashboard.

**Runs on LangGraph Agent Server.** The compiled deep agent (`graph.py`) is deployed
via `langgraph.json`; the SPA talks directly to the server's `/threads` +
`/runs/stream` (`stream_mode: "messages"`). As the agent streams, each `push_widget`
tool call's args fill in and the frontend renders that widget the moment it looks
complete — so the dashboard builds **one widget at a time**, then the final answer
streams in. Every widget is re-validated against the Pydantic schema server-side.

**Assistants = demo variants.** One graph, many [assistants](https://docs.langchain.com/langsmith/assistants)
(configuration instances) set the `Context` (prompt / dataset / model) — e.g.
"Humanitarian (bundled corpus)" vs "Synthetic — any topic". Switch variants by
`assistant_id`; no redeploy. See `scripts/seed_assistants.py`.

**UI:** loads as a centered chat; once the first widget streams in, the chat slides to
a left rail and the dashboard canvas reveals on the right. A **Download PDF** button
exports the canvas via html2pdf. A ⚙️ gear customizes name/color/logo, the quick-action
questions, and the deployment URL + assistant.

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

# create the assistant variants (server must be up), then note their ids
uv run python scripts/seed_assistants.py

# open the SPA (http://127.0.0.1:3000), set the deployment URL + assistant via ⚙️
# (or edit static/config.js), and ask a question.
```

`uv run ./run.sh` syncs the environment, then starts `langgraph dev` plus the
front-end. It bootstraps what's missing: `uv sync --group dev` if there's no venv
or no `langgraph` CLI, and `npm ci` in `frontend/` if `node_modules` is absent. It
warns (but still starts) when `.env` is missing. Plain `./run.sh` works too once
you've synced — the `uv run` prefix just guarantees the env is current first.
Set `PORT` / `SPA_PORT` to override the ports.

## Tests

```bash
# fast (no LLM): rag, widgets, SQL, mocked server, streaming logic
python -m pytest dashboard_agent/tests/test_rag.py dashboard_agent/tests/test_widgets.py dashboard_agent/tests/test_database.py dashboard_agent/tests/test_streaming_unit.py -q

# frontend pure-logic (Node)
node dashboard_agent/tests/frontend_test.js

# real agent e2e across all 3 personas (slow, ~2 min, costs tokens)
python -m pytest dashboard_agent/tests/test_agent_e2e.py -v

# hallucination-bug before/after (slow)
python -m pytest dashboard_agent/tests/test_hallucination_bug.py -v
```

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

The `datasearch` / `query_sql` tools go through a pluggable `DataSource`
(`datasource.py`):

- **`humanitarian`** (default) — the bundled corpus: real TF-IDF + real SQLite.
- **`synthetic`** — a fast LLM *stands in* for the backend and invents plausible
  data per call. `query_sql` passes the raw SQL straight to the model, which
  "executes" it and returns rows. The **topic and the planted gap live in a second
  Prompt Hub prompt** (`dashboard-agent-data`), so you can point the demo at any
  domain and control what data exists — live, no redeploy.

The data source is chosen per **assistant** via its `Context` (`dataset`,
`data_model`, `data_prompt_name`). `scripts/seed_assistants.py` creates a
"Synthetic — any topic" assistant; select it in the SPA (⚙️) to run in this mode.
For a purely local (non-deployment) run, `DASHBOARD_DATASET=synthetic` is the env
fallback the tools read when no context is set.

The grounding story is preserved: the data prompt withholds the trap figure (e.g.
"schools rebuilt"), the tool returns nothing, and the main agent's buggy prompt
fabricates over the gap — exactly the same catch-and-fix demo, now domain-agnostic.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | (from `.env`) | required — agent model |
| `LANGSMITH_API_KEY` | (from `.env`) | required — pull the prompt from Prompt Hub + feedback |
| `WORKSPACE_ID` | (API key's workspace) | LangSmith workspace/tenant to scope prompts, traces, and feedback |
| `PROJECT_NAME` | `dashboard-agent` | LangSmith tracing project for agent runs |
| `DASHBOARD_MODEL` | `claude-sonnet-4-5-20250929` | agent model |
| `DASHBOARD_PROMPT` | `dashboard-agent-system` | Prompt Hub name to pull the system prompt from |
| `DASHBOARD_DATASET` | `humanitarian` | `synthetic` = live-LLM data backend for any topic |
| `DASHBOARD_DATA_MODEL` | `anthropic:claude-haiku-4-5-20251001` | fast model for synthetic data (`init_chat_model` id) |
| `DASHBOARD_DATA_PROMPT` | `dashboard-agent-data` | Prompt Hub name for the synthetic data prompt |
