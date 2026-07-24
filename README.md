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
  widgets.py    # Pydantic widget schemas (kpi/bar/line/pie/table/text) + validation
  prompt.py     # system prompt sourced from LangSmith Prompt Hub (+ local fallback)
  agent.py      # deep agent: datasearch + query_sql + push_widget; run() + run_stream()
  server.py     # FastAPI: POST /api/chat(+/stream), GET /api/health, serves the SPA
  static/       # index.html + app.js (progressive Chart.js canvas + streaming chat)
  tests/        # unit, mocked-server, streaming-logic, real-agent e2e, Node, hallucination
```

**Tools the agent has:**
- `datasearch` — in-memory RAG for grounded prose + structured data.
- `query_sql` — read-only SQL SELECT over an in-memory SQLite DB (rankings, deltas, time series).
- `push_widget` — appends one validated visualization to the dashboard.

**How the agent builds UI (streaming):** the frontend calls `POST /api/chat/stream`,
which returns newline-delimited JSON (NDJSON). `run_stream` reads the agent's
token-level stream and emits an event the instant each `push_widget` call's arguments
finish streaming — so the dashboard builds **one widget at a time**, then the answer
streams in token-by-token:

```
{"type":"widget","widget":{...}}          # each widget as it is built
{"type":"answer_delta","text":"...","mid":"..."}   # answer streams in
{"type":"done"}
```

Every widget is re-validated against the Pydantic schema before it is emitted, so a
malformed spec from the model never reaches the browser. A non-streaming
`POST /api/chat` → `{"answer": ..., "widgets": [...]}` is also available.

**UI:** loads as a centered chat; once the first widget streams in, the chat slides to
a left rail and the dashboard canvas reveals on the right. A **Download PDF** button
(next to "Live dashboard") exports the canvas via html2pdf (print fallback).

## Run it

```bash
# from dashboard-agent/
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# provide your keys (see .env.example)
printf 'ANTHROPIC_API_KEY=sk-ant-...\nLANGSMITH_API_KEY=lsv2_...\n' > .env

# seed the system prompt into LangSmith Prompt Hub (once)
python scripts/seed_prompt.py

python -m uvicorn dashboard_agent.server:app --port 8137
# open http://127.0.0.1:8137
```

Or just `./run.sh` (auto-detects a local `.venv`).

## Tests

```bash
# fast (no LLM): rag, widgets, SQL, mocked server, streaming logic
python -m pytest dashboard_agent/tests/test_rag.py dashboard_agent/tests/test_widgets.py dashboard_agent/tests/test_database.py dashboard_agent/tests/test_server.py dashboard_agent/tests/test_streaming_unit.py -q

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

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | (from `.env`) | required — agent model |
| `LANGSMITH_API_KEY` | (from `.env`) | required — pull the prompt from Prompt Hub + feedback |
| `WORKSPACE_ID` | (API key's workspace) | LangSmith workspace/tenant to scope prompts, traces, and feedback |
| `PROJECT_NAME` | `dashboard-agent` | LangSmith tracing project for agent runs |
| `DASHBOARD_MODEL` | `claude-sonnet-4-5-20250929` | agent model |
| `DASHBOARD_PROMPT` | `dashboard-agent-system` | Prompt Hub name to pull the system prompt from |
