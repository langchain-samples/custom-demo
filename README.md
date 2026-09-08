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

📹 **[Watch the walkthrough](https://www.loom.com/share/d5ce4bb5a2b5485baef75d0a1d84f825)** (Loom)

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
mcp_demo_server/# a local FastMCP server to connect the agent to (stateless spec, elicitation,
                #   an MCP App); run it with scripts/run_mcp_server.sh --tunnel
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

- **Remote MCP servers** — tools this repo does not own. Paste a server URL into
  **Settings → MCP servers**, press Test, and its tools are in play on the next message. See
  [Connecting an MCP server](#connecting-an-mcp-server) below.

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

**Prerequisites**, none of which this repo can install for you:

| | | |
| :-- | :-- | :-- |
| **uv** | required | `curl -LsSf https://astral.sh/uv/install.sh \| sh` — also provisions Python |
| **Python ≥ 3.13** | required | uv installs it; a system Python only matters if you skip uv |
| **Node 20+ / npm** | for the UI | the SPA is a Vite app. The agent and its evals run without it |
| **LangSmith key** | required | tracing, Prompt Hub, and the demo evals |
| **A model provider key** | required | Anthropic by default; any `init_chat_model` provider works |

```bash
# from dashboard-agent/
uv sync --group dev          # dev group includes langgraph-cli[inmem] + langgraph-sdk

# provide your keys (see .env.example)
cp .env.example .env         # then edit: LANGSMITH_API_KEY + one model provider key

# check the setup BEFORE anything else — it names what's missing and how to fix it
uv run python scripts/preflight.py

# seed the Prompt Hub prompts (once)
uv run python scripts/seed_prompt.py         # system prompt (starts buggy)
uv run python scripts/seed_data_prompt.py    # synthetic data prompt (optional)

# start Agent Server (:2024) + the React SPA (:3000)
uv run ./run.sh

# open the SPA (http://127.0.0.1:3000), then in ⚙️: pick a Workspace, hit "+ New"
# to set up a customer assistant, and ask a question.
```

`preflight.py` makes one cheap real model call and one LangSmith round-trip, then
prints `ALL CHECKS PASSED` or the specific fix. Run it first; most setup problems
show up there rather than as a stack trace ten minutes into a demo.

### If something doesn't work

| Symptom | Cause |
| :-- | :-- |
| `ANTHROPIC_API_KEY is not set` on a non-Anthropic setup | `DASHBOARD_MODEL` still defaults to Anthropic. Set it to your `provider:model` and that provider's key. |
| Azure calls 404 | `AZURE_OPENAI_ENDPOINT` includes `/openai/deployments/...`. It must stop before that; the client appends it. |
| The model rejects `temperature` | Reasoning-tuned models allow only their own default. Set `DASHBOARD_TEMPERATURE=` (empty) to omit it. |
| Editing the prompt in the Hub changes nothing | You edited a different prompt. `+ New` gives each assistant its own `<slug>-system`; `seed_prompt.py` writes the shared default. |
| Model calls go somewhere unexpected | `ANTHROPIC_BASE_URL` is set in your shell and redirects everything. Preflight warns about this. |

**Running this with a group.** Each `+ New` assistant gets its own Prompt Hub prompt
(`<slug>-system`) and its own eval dataset, so people don't overwrite each other **as
long as they pick distinct customer names**. `seed_prompt.py` is the exception: it
writes one shared prompt, so it only needs running once per workspace.

`scripts/seed_assistants.py` still exists if you want two bare variants
("Humanitarian (bundled corpus)" / "Synthetic — any topic") without the branding flow.

`uv run ./run.sh` syncs the environment, then starts `langgraph dev` plus the
front-end. It bootstraps what's missing: `uv sync --group dev` if there's no venv
or no `langgraph` CLI, and `npm ci` in `frontend/` if `node_modules` is absent. It
warns (but still starts) when `.env` is missing. Plain `./run.sh` works too once
you've synced — the `uv run` prefix just guarantees the env is current first.
Set `PORT` / `SPA_PORT` to override the ports.

## Connecting an MCP server

Give an assistant tools this repo does not own. Paste a server's URL into
**Settings → MCP servers**, press **Test** to see the tools it advertises, and they are in play
on the next message. The connection is saved on the assistant, so it is per-customer config like
branding or the prompt.

**Do I need ngrok?** Only for a *deployed* agent. The agent connects **outbound** to the URL you
give it, so `localhost` inside the deployment's container is the container, not your machine. A
server running on your laptop therefore needs a public address. Running the agent locally
(`./run.sh`) needs no tunnel at all.

There are two demo servers in the box, one per business, because a logistics book with a
portfolio rebalancer in it is not a demo anyone believes:

```bash
./scripts/run_mcp_server.sh --wealth --tunnel   # Meridian Wealth  (the advisory demo)
./scripts/run_mcp_server.sh --tunnel            # Fieldlink Logistics
./scripts/run_mcp_server.sh --wealth            # local only: http://127.0.0.1:8765/mcp
```

Paste the printed URL (including the `/mcp` path) into Settings. A new hostname is issued per
run, so re-paste after a restart, and put a bearer token on anything you leave up — a tunnel is
public.

**Prefer cloudflared over ngrok** (`brew install cloudflared`; the script picks it automatically
when present). Not a style preference: ngrok's free tier answers any request carrying a browser
User-Agent with an interstitial warning page instead of the resource, so a signature image
embedded in a generated document renders broken — and an `<img>` tag cannot send the
`ngrok-skip-browser-warning` header that would opt out. MCP itself works fine either way, since
the client is not a browser.

**Meridian Wealth** (`mcp_demo_server/wealth.py`) is a pretend advisory platform. Every
interactive tool is an MCP App, because each collects something a generated form cannot:

| tool | the UI it ships |
|---|---|
| `list_accounts`, `get_account` | none - ordinary tools, and an App there would be decoration |
| `propose_rebalance` | allocation sliders constrained to total 100%, with drift from policy and estimated tax drag recomputing as you drag |
| `project_goal` | retirement age, contribution and risk sliders over a projection band that redraws live (the maths runs in the app, so it does not wait on a round trip) |
| `confirm_trade` | an order ticket with a quantity stepper, market/limit, time in force, and **hold to confirm** |
| `sign_document` | the signature pad |

The point of `confirm_trade` is not the widget: an irreversible action gets a real confirmation
surface instead of the model interpreting the word "yes". Every constraint the app enforces is
enforced again server-side (an allocation must total 100%, a sale cannot exceed the position) -
the app is a UI, not a boundary.

**Fieldlink Logistics** (`mcp_demo_server/server.py`) is a pretend field-operations system,
written against the same spec, and the one the walkthrough below uses:

| tool | what it shows |
|---|---|
| `find_shipments`, `get_shipment` | ordinary tools; the tool list is **cacheable**, so discovery is not a round trip per run |
| `schedule_delivery` | **elicitation** — the server stops mid-call to ask for a date and window, and the SPA renders a form built from the schema it asked for |
| `collect_signature` | an **MCP App** — the server ships a signature pad as a `ui://` HTML resource, and the SPA renders it in a sandboxed iframe. Its result is **multimodal**: the drawn signature comes back as an image block the model can see, plus a `signature_url` it can embed |

Tools arrive namespaced by server (`fieldlink_get_shipment`), which keeps them clear of the
built-in catalogue and shows where each one came from.

An elicitation is a genuine pause: the run stops, you answer in chat, and the server's tool
resumes and finishes with your answer. In the signature case you draw on the pad, hit Confirm,
and the tool returns a proof-of-delivery record the agent then reports. Nothing about that is
specific to Fieldlink — any MCP server that elicits gets the generic form for free, and any tool
that declares a `ui://` resource gets rendered.

**Getting the signature into a document.** Ask for a proof-of-delivery document and the agent
writes an HTML artifact containing `<img src="https://<tunnel>/signatures/FL-4417.png">` (see the
ngrok caveat above if it renders broken). The
image is never inlined as base64, for two reasons: it is thousands of tokens on every subsequent
turn, and a model cannot retype 10KB of base64 without corrupting it. The bytes stay on the MCP
server and travel as a URL. The model is still *shown* the signature as an image block, so "what
does the signature look like?" is a question it can answer.

Writing your own is worth knowing four things about, three of which cost an hour each to find:

- Use the **guard pattern** (return an `InputRequiredResult`) rather than `ctx.elicit()`, which
  the stateless protocol cannot deliver at all.
- The tool **re-runs from the top** when the answer comes back, so do no real work before you ask.
- **Elicitation content is flat.** `ElicitResult.content` allows primitives only, so a schema
  cannot ask for a nested object: the rebalance sends one number per sleeve, not an `allocation`.
- **An app's render context goes on a schema PROPERTY, never the root.** The SDK normalizes
  `requested_schema` and silently drops unknown root keys, so context at the root vanishes with no
  error and the app renders empty. `elicit.attach_context` is the one place that knows this.

`mcp_demo_server/` is commented as a worked example, and `apps/bridge.js` is the postMessage
plumbing all four apps share.

## Voice mode (spike)

Talk to an assistant while the dashboard fills in: a Gemini Live shell in front of the
deep agent, with the tool executed in the browser so the canvas, chips and trace all come
from the normal run. Off unless an assistant's builder flag turns it on, and invisible
without `GEMINI_API_KEY`. See [docs/voice-mode.md](docs/voice-mode.md).

## About panel

The header's ⓘ opens "About this agent": what this assistant is, the recommended demo
flow, and an architecture diagram. The
customer-specific half is `metadata.demo_brief` / `demo_flow`, written per customer by
`build_demo_brief()` at setup, and rendered through the same components as the
post-setup popup (`settings/BriefLists.tsx`) so the two cannot drift. Reopenable, which
is what was missing: the brief used to appear once, right after setup, and then never
again.

Ported from the Super Group build (`joel-langchain`, `9115f8a`) minus its copy, which
named that customer's brands and warehouses in shared prose.

## Graph mode

A live, left-to-right graph of the agent working, opened from the header's graph icon as
a **floating inspector**: draggable, resizable, and remembered (position, size and open
state all persist). Lanes (plan, skills, sandbox, data, web, human, delegate, output) are
drawn only when they actually fire, each carries a one-line hint saying what it is, nodes
go solid as each tool result lands, and clicking one shows the argument and the output.

It floats rather than sitting in the right-hand pane because the two have different
audiences: the pane is output for whoever is watching the demo, and this is introspection
for whoever is driving it. A panel you drag into place reads as an operator's tool. It is
deliberately NOT a Radix dialog either - those are modal, and a focus trap would stop you
typing in the chat while the graph fills in, which is the whole reason it is not a tab.

It is a pure RENDERER over state the chat rail already streams: `ChatPanel` mirrors the
current question's chips and subagent groups out through `onActivity`, and nothing feeds
back, so it cannot change what the agent does. Ported from the Super Group build
(`joel-langchain`, `9115f8a`).

Subagent lanes appear whenever the agent dispatches `task`, which deepagents' built-in
general-purpose subagent does out of the box. `DA_DYNAMIC_SUBAGENTS=1` is a different
thing: it swaps that generalist for the named `researcher` / `analyst` pair and adds JS
orchestration, so the lanes get real names and several can run at once. It is off in
production.

## HTML artifacts

Widgets are the answer surface, and the prompt says so: the agent reaches for
`push_widget` whenever a KPI, chart, table or text block can make the point. For the
things those six types genuinely cannot express (a formatted letter, a print-ready
report, a page to download and send on, an interactive view) it writes a standalone
document with `write_file` to `/workspace/artifacts/<name>.html`.

That file becomes its own tab beside the dashboard, and it renders **while it is being
written**. No new tool and no new widget type: the frontend already partial-parses
streaming tool-call arguments to fill in charts progressively, and the artifact tab
reads `write_file`'s `content` argument the same way. Because the artifact is a real
file, the agent revises it with `edit_file` instead of rewriting it, and the existing
file browser and `/sandbox-file` route serve it unchanged.

**Write order matters, and the prompt says so.** The tab renders as the agent types, so
anything before the first visible element is time on a placeholder - and a full stylesheet
in `<head>` measured 30-44% of every artifact produced so far (4000-5966 bytes of CSS).
The prompt asks for a short critical `<style>` in the head, then the body, then the rest
of the CSS before `</body>`. CSS applies whenever it arrives, so the document is readable
almost immediately and finishes polished. Until content exists the tab shows a
document-shaped skeleton plus a rotating status line.

`safeHtmlPrefix` (`frontend/src/lib/artifacts.ts`) is what makes a half-written document
renderable. HTML5 parsing recovers from unclosed elements on its own, so it only repairs
the three cases that do not: a half-written tag, an unclosed `<style>`, and an unclosed
`<script>` (emptied, since half a statement throws).

The iframe runs with `allow-scripts` and deliberately **without** `allow-same-origin`, so
a generated page executes and can load external CDNs but cannot read the deployment
token this app keeps in `localStorage`. Those two sandbox flags must never appear
together there.

"Save as PDF" goes through the browser's print pipeline (`contentWindow.print()`, which
is why the sandbox carries `allow-modals`) rather than a JS library. html2pdf, which the
widget dashboard uses on its own trusted DOM, could not be given a DOM here without
injecting model-authored HTML into the parent document, and it rasterizes: its output is
a picture of the page, with no selectable text. Printing yields real vector text and
respects the document's `@media print` rules, at the cost of a save dialog.

**Needs the sandbox.** Without one (`DA_SANDBOX=0`, no entitlement) `write_file` goes to
the graph `files` state key rather than a VM. The live stream still renders, since it
reads the tool argument, but the post-write canonical re-read has nothing to fetch, so
an `edit_file` result will not be reflected.

## Tests

```bash
# fast (no LLM): rag, widgets, streaming logic, tool registry + selection middleware
uv run pytest dashboard_agent/tests/test_rag.py dashboard_agent/tests/test_widgets.py \
              dashboard_agent/tests/test_streaming_unit.py \
              dashboard_agent/tests/test_tool_registry.py -q

# MCP: config parsing + caching, and the demo server driven in-process (no socket)
uv run pytest dashboard_agent/tests/test_mcp_servers.py -q

# frontend pure logic (Node — imports the real .ts modules via native type stripping)
node dashboard_agent/tests/branding_test.js    # colour maths, contrast, chart palette
node dashboard_agent/tests/trace_test.js       # trace-project naming
node dashboard_agent/tests/signature_app_test.js  # the MCP App's postMessage contract, in jsdom

# frontend typecheck / lint / build / component tests
cd frontend && npx tsc -b && npx oxlint && npm run build && npm test

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
| `TAVILY_API_KEY` | (unset) | required by the optional `web_search` tool; without it that tool returns an error instead of results. The deployment reads it from its own `TAVILY_API_KEY` secret on the LangSmith deployment (there is no CD job; see the comment in `.github/workflows/ci.yml`) — a key in your local `.env` does **not** reach it |
| `BRANDFETCH_API_KEY` | (unset) | optional — accurate brand palette + typefaces at setup; falls back to an LLM guess |
| `LOGODEV_TOKEN` | (bundled publishable key) | optional — Logo.dev key for customer logos |
| `LANGGRAPH_URL` | `http://127.0.0.1:2024` | Agent Server the `scripts/` helpers talk to |
