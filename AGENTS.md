# AGENTS.md — Dashboard Agent (the "Corebot" custom-demo backend)

Orientation doc for anyone (human or agent) working in this repo. It describes **what
exists today**, how it maps to the original Corebot proposal, and where the two diverge.

---

## 1. What this is

One **LangGraph deep agent** that answers a question by *building a live dashboard* —
it retrieves data, emits a stream of validated widget specs (KPI cards, charts, tables,
key-findings text), then writes a short narrative answer. A React SPA renders each widget
the moment its tool-call args finish streaming, so the dashboard assembles one card at a time.

Per-customer customization is done with **LangGraph Platform assistants** — configuration
instances of one shared graph. No new app, no redeploy, per demo. This is Proposal 1 of the
Custom Demos doc, implemented.

There is also a deliberate, live-fixable **hallucination demo**: the data source withholds one
customer-specific metric, the system prompt tells the agent to fabricate confidently over gaps,
and you "fix" it by editing the prompt in LangSmith Prompt Hub mid-demo. Each assistant also gets
its own **LangSmith eval dataset** that scores that arc live — 2/3 passing before the fix, 3/3
after (§3, *Per-assistant demo evals*; mind the polarity, it is the reverse of `evals/`).

---

## 2. Repo map

```
dashboard_agent/
  agent.py            deep agent: Context schema, middleware, run/run_stream
  ctx.py              ctx_get() — reads a Context field off a runtime (dict or dataclass)
  tools/registry.py   THE TOOL CATALOGUE — declarative source of truth for selectable capabilities
  tools/core.py       datasearch + push_widget (and the widget ContextVar sink)
  tools/simulated.py  capability tools: draft_email, suggest_meeting_times, list_data_sources
  tools/web_search.py web_search — REAL results via Tavily (errors out without TAVILY_API_KEY)
  graph.py            Agent Server entrypoint — async factory that wraps runs in tracing_context
  setup_graph.py      SECOND graph (`assistant_setup`): prepares a new customer assistant
  assistant_setup.py  brand fetch (Logo.dev/Brandfetch/scrape) + LLM customer analysis + prompt push
  assistant_evals.py  per-assistant demo eval: EVAL_MODES registry, dataset upsert, experiment
                      run, and the evaluator (score 1 = CORRECT — the OPPOSITE of evals/)
  prompt.py           prompt construction + Prompt Hub pulls + the hallucination/grounding clauses
  config.py           env loading, model/prompt/workspace/dataset accessors, LangSmith client
  datasource.py       pluggable backend behind `datasearch`: static corpus | synthetic LLM
  corpus.py / rag.py  bundled humanitarian corpus + dependency-free TF-IDF retriever
  widgets.py          Pydantic widget schemas — the agent↔frontend contract
  webapp.py           extra Starlette routes: /feedback /projects /workspaces /hub-prompts /tools
                      /sandbox-files /sandbox-file (read-only browse of the assistant's VM)
                      /evals/run + /evals/status (per-assistant demo eval), /cleanup, /trace-url
  static/             LEGACY vanilla-JS SPA (superseded by frontend/, still on disk)
  tests/              rag, widgets, streaming, tool-registry, eval examples/polarity/routes (fast)
                      + e2e, hallucination (slow)
frontend/             React 19 + Vite + Tailwind 4 + shadcn SPA (the real UI)
  src/lib/branding.ts   brand seeds → CSS vars; resolveColor, contrast, chart-palette derivation
  src/lib/fonts.ts      Google-Fonts loader + curated self-hosted fallbacks
evals/                repo-level Tier-3 LLM evals, run by us before a release — score 1 = the
                      planted BUG fired. Not the per-assistant demo eval; see evals/README.md
scripts/              seed_prompt, seed_data_prompt, seed_assistants, setup_assistant, serve_spa
.claude/skills/setup-assistant/SKILL.md   interactive /setup-assistant flow (CLI path)
langgraph.json        registers both graphs + http.app + wide-open CORS
pyproject.toml        Python deps + dev group (uv); uv.lock pins them
run.sh                langgraph dev (:2024) + Vite (:3000)
```

Python dependencies are managed with **uv** (`pyproject.toml` + `uv.lock`, `.python-version`);
`requirements.txt` is gone. Use `uv sync --group dev`, not `pip install -r`.

## 3. Runtime architecture

**Graphs (2, same server):**
- `dashboard_agent` → `graph.py:graph`. An `@asynccontextmanager` **factory**: reads
  `configurable.ls_workspace` / `ls_project` per run and wraps the (once-built) compiled graph in
  `tracing_context(client=…, project_name=…)`. This is how each customer's traces land in their
  own LangSmith workspace/project.
- `assistant_setup` → `setup_graph.py:graph`. A trivial one-node StateGraph wrapping
  `prepare_assistant()`. The SPA calls it via `runs/wait`, then creates the assistant from the
  payload it returns.

**The agent** (`agent.py`, built by `deepagents.create_deep_agent`):
- **Tools come from two independent sources.** deepagents *always* installs its own —
  `write_todos`, the filesystem set (`ls`, `read_file`, `write_file`, `edit_file`, `glob`,
  `grep`) and `task` — because `TodoListMiddleware`/`FilesystemMiddleware` are unconditional
  and a default general-purpose subagent is auto-added. `execute` is now offered too — the
  agent's default backend is a code-execution sandbox VM (see **Code execution** below). On top
  of those sits **our catalogue** (`tools/registry.py`), which is the only part an assistant
  can select from.
- Middleware: `ConfigurableModel` (swap LLM from `context.model`), `_hub_system_prompt`
  (`@dynamic_prompt` — pulls the prompt fresh per question, then appends the enabled
  capabilities), the registry's `ToolCallLimitMiddleware` instances (`datasearch` is capped at
  1 call/run — extra searches mask the planted gap), and `ToolSelection` **last**.
- Model is `ChatAnthropic` with `thinking={"type":"disabled"}` — Sonnet 5's default extended
  thinking breaks the deep-agent tool loop on follow-up turns.

**Code execution (sandbox) + universal skills.** `_backend_for` builds ONE `CompositeBackend`:
the **default** is an isolated LangSmith sandbox VM (so the model gets an `execute` tool + a real
filesystem — `pip install --break-system-packages pandas numpy`, run analysis/forecasts, write
outputs, then chart via `push_widget`), and `/skills/` is **routed to the assistant's Context Hub
skills-bundle repo** (live read/write). Two deepagents constraints force this shape:
- `execute` is offered only when the `CompositeBackend`'s *default* is a sandbox (execute isn't
  path-routable) — so the sandbox must be the default.
- a composite route strips its prefix, so the mounted skills repo must store skills at its **root**
  (`<name>/SKILL.md`), not under `skills/`. Hence a dedicated per-assistant `*-skills` bundle repo
  (see `assistant_setup.push_skills_bundle`), not the agent repo.

**Every generated skill uses both capabilities.** Setup's LLM call returns, per skill, a `workflow`
(one of `WORKFLOW_PATTERNS` — the dynamic-subagent shapes) and a `sandbox_step` (which seeded file
to open with `execute`, and what to compute); `_skill_md` appends a section for each. Both sections
are emitted **unconditionally** — an unset/unknown `workflow` falls back to `_DEFAULT_WORKFLOW`
rather than dropping the fan-out. This is deliberate: a quick action invokes a skill, so the skill
body is where "this demo shows dynamic subagents and code execution" is actually enforced. Since
`sandbox_step` names a file, the same call also proposes the `seed_files` planted in the VM, and the
prompt tells it to keep the two consistent.

**Skills are universal**: every assistant gets a `*-skills` bundle regardless of whether its prompt
lives in Prompt Hub or Context Hub (that choice is only about prompt storage). `context.skills_repo`
names the bundle; `context.agent_repo` (if set) only holds the prompt's AGENTS.md. For skills to
reach the model, `_hub_system_prompt` composes deepagents' middleware prompt (the SkillsMiddleware
catalogue + filesystem/execute instructions) whenever `skills_repo` or `agent_repo` is set.

The VM is **assistant-scoped and cached** (`_SANDBOX_CACHE`), since the backend factory is resolved
on every model/tool call; idle VMs self-reap via TTL, and a fresh VM is seeded from the
assistant's own `context.sandbox_seed` spec at `/workspace/data/` (the synthetic 24-month
`sales.csv` is only the fallback for an assistant with no spec). Degrades gracefully: no `[sandbox]` extra, no
`LANGSMITH_API_KEY`, or `DA_SANDBOX=0` → StateBackend default (no `execute`), skills still mount.
**Back-compat:** a pre-existing Context Hub assistant has `agent_repo` but no `skills_repo`; it
keeps the whole-repo `ContextHubBackend` (skills under its `skills/`, no execute) until recreated.

**Browsing that VM from the SPA.** A toolbar button opens a near-fullscreen dialog (`FileBrowser`
→ `SandboxBrowser`) with a lazy file tree on the left and a viewer on the right, over
`GET /sandbox-files` (one directory per request, 500-entry cap) and `GET /sandbox-file` (one
page of one file, `limit` lines, `next_offset` for "Show more"). Two rules shape it:
**attach-only** — `_ensure_sandbox(key, create=False)`, so a UI click can never provision a VM
(~30s boot + pip install) and "no sandbox" is a calm 503 the dialog renders as copy; and
**read-only, allowlisted** — extensions outside `_TEXT_EXTS` (and anything named `.env*`) never
reach the VM, and the browsable root is `DA_FILES_ROOT` (default `/workspace`). The dialog's
Refresh is a remount, so there is no cache-invalidation code.

**Goals (`/goal`) and rubric grading.** Typing `/goal <what done looks like>` in the composer is a
CLIENT-SIDE command (`lib/commands.ts` parses it; ChatPanel `handleGoalCommand` acts on it) that
raises a pill above the composer and rides along with every subsequent turn as the run input's
`rubric`. Setting a goal ALSO runs it as that turn's question — "/goal build me a dashboard" means
both "here is what done looks like" and "off you go". The parser accepts `/goal` mid-sentence
("set a /goal to …"), which is how people type it; anchoring only at the start silently sent those
through as ordinary questions. Typing `/` opens a command palette and a completed command shows as
a token above the composer, so a mistyped one is visibly not a command before it is sent. deepagents'
`RubricMiddleware` (built in `agent._rubric_middleware`, graded by `config.goal_model()`, capped by
`goal_max_iterations()`) then grades each finished turn against it and jumps the agent back to the
model with per-criterion feedback until the grader is satisfied. Three things make this work:
- The middleware is **always installed but inert** — both hooks no-op without a `rubric` — so it is
  not env-gated, and it is FIRST in the middleware list so its `after_agent` runs last (after hooks
  fire in reverse order), making it the final say on whether a turn is done.
- The grader's own model call streams on the parent's channel under a
  `RubricMiddleware.after_agent:<uuid>` namespace. Its frames are AI messages with no tool calls,
  i.e. the exact shape of a final answer, so ChatPanel drops anything `isMiddlewareNamespace` before
  routing — otherwise the verdict JSON renders in the chat as the assistant's reply. (The revision
  feedback the middleware injects IS a real `HumanMessage` on the thread, by design: the agent has
  to read it. The UI ignores human frames, so it stays invisible.)
- The pill's states come from the grader's `rubric_evaluation_*` frames on the **`custom`** stream
  mode (hence its addition to `runStream`), not from state: the bookkeeping keys are `PrivateStateAttr`
  and never reach the client. `satisfied` clears the pill after a short linger; the iteration cap and
  grader errors collapse into one "not met" state.
- The rubric is sent on EVERY turn, **empty when there is no goal**. State is checkpointed, so merely
  omitting the key after the user clears the pill would leave the thread grading forever.

**The tool catalogue** (`tools/registry.py`). One `ToolSpec` table drives the settings UI
(`GET /tools`), the run-time filter, and the per-tool call caps. Selection lives in
`context.enabled_tools`.

| id | group | notes |
|---|---|---|
| `push_widget` | Dashboard | `always_on` — the dashboard depends on it |
| `datasearch` | Data | on by default; capped at 1 call/run |
| `list_data_sources` | Data | simulated "connected systems" list |
| `draft_email` | Comms | simulated draft, rendered as a chat card |
| `suggest_meeting_times` | Comms | simulated slots, rendered as a chat card |
| `web_search` | Research | REAL results via the Tavily API; returns an error (never invented results) if `TAVILY_API_KEY` is unset |
| `ask_user` | Interaction | HITL: pauses via `interrupt()` to ask the user a multiple-choice question (model supplies the `options`), resumes with the option they pick (renders a question card) |

Capabilities are chosen in the **"+ New" form** when creating an assistant, and stay editable
afterwards in **Settings → Tools** — changing them is a config edit on the existing assistant
(`PATCH` its `context`), NOT a reason to create a new one and never a redeploy. Only *adding a
tool to the catalogue* needs a code change.

Selection is enforced **server-side** by `ToolSelection`, which filters `request.tools` at
model-call time (`request.override(tools=…)` — the same mechanism deepagents uses to drop
`execute`). Two invariants hold it together:
- `allowed_tool_names(None)` returns `{datasearch, push_widget}` — exactly the pre-catalogue
  behaviour, so assistants created before this feature are untouched.
- `is_allowed()` passes through **any name the catalogue doesn't declare**, which is what
  leaves the deepagents built-ins alone and makes a future deepagents upgrade safe.
- `[]` means "every optional tool off" and is NOT the same as unset. Three layers must agree
  (`parse_enabled`, `resolveRunContext`, the PATCH) or turning everything off silently
  restores the defaults.

The four simulated tools follow the `SyntheticDataSource` pattern — a fast LLM invents
customer-tailored content from `context.customer`/`industry`. They render as typed cards in
chat (`frontend/src/components/chat/ToolResultCard.tsx`); anything dashboard-worthy goes
through the existing `push_widget` types rather than a new widget schema.

**Human-in-the-loop.** `draft_email` and `suggest_meeting_times` generate, then call
`interrupt()` (via `review()` in `tools/simulated.py`) — the run genuinely PAUSES. The payload
arrives on the stream's `updates` event as `__interrupt__`, `ChatPanel` renders
`chat/ReviewCard.tsx` (an editable email form / a slot picker with a `datetime-local` control),
and approving resumes the thread with `command: {resume: …}`. The tool returns the human's
version, so the agent's final answer reflects the edit.

Two things to know before touching it:
- **Resuming re-executes the whole node**, so a generate-then-interrupt tool would run its LLM
  call twice. `_pending`, keyed by `tool_call_id`, makes the second pass reuse the first pass's
  output and fall straight through to the answered interrupt.
- The run body uses `stream_mode: ["messages", "updates"]`, and a resume sends `command`
  **instead of** `input` — sending both duplicates the user turn.

**Trace project** is `<client>-corebot-demo` (`frontend/src/lib/trace.ts`, mirrored by
`prepare_assistant`). Suffixed so demo traces are obvious in a shared workspace and can't
collide with a real project of the same name; an explicit `context.ls_project` overrides it.

**`Context` — the whole per-assistant behavior surface** (`agent.py`):

| field | purpose |
|---|---|
| `model` | main agent LLM id |
| `prompt` / `prompt_name` | inline system prompt (wins) or a Prompt Hub handle |
| `agent_repo` | Context Hub repo whose AGENTS.md is the prompt (prompt storage only) |
| `skills_repo` | Context Hub `*-skills` bundle mounted at `/skills/` (all assistants) |
| `dataset` | `humanitarian` \| `synthetic` |
| `data_model`, `data_prompt`, `data_prompt_name` | synthetic-backend model + prompt |
| `data_gap` | the withheld topic (builds a customer-centric data prompt) |
| `customer`, `industry` | steer synthetic data + prompt templating |
| `ls_workspace` | trace routing + which workspace's Prompt Hub to pull from |
| `enabled_tools` | catalogue tool ids to expose (`None` = defaults, `[]` = optional all off) |

Everything else (middleware, checkpointer, backends, permissions, and the *implementation* of
any tool) is **locked in code** — matching the plan's security boundary. Assistants pick from a
vetted catalogue; they cannot introduce a tool, and there is no code path where assistant
config can select a filesystem/shell backend. (The default backend is now a LangSmith
code-execution sandbox — chosen in code by `_backend_for`, still never selectable via config;
`DA_SANDBOX=0` is the code-side kill switch.)

**Display config lives separately, in the assistant's `metadata`:** `display_name`, `logo`,
`actions[]`, `theme`, `owner_name`, `customer`, `industry`, plus the brand system —
`accent`, `accent2`, `brand_neutral`, `brand_tint`, `font_heading`, `font_body`,
`font_heading_fallback`, `font_body_fallback`, `font_source`. The SPA reads it via the
assistants API and debounce-PATCHes edits straight back — so branding is server-side and
reusable across DEs, as the plan required.

**Branding** (`frontend/src/lib/branding.ts`, `fonts.ts`, `index.css`). Two rules, both of
which cause silent bugs when broken:
1. **JS only writes theme-INDEPENDENT values.** Inline styles on `documentElement` beat both
   `:root` and `.dark` permanently, so a theme-dependent value written from JS would be wrong
   in one theme forever. Where a value must vary (the derived chart series) JS writes *both*
   `--chart-N-light` and `--chart-N-dark` and the cascade picks.
2. **Never read a custom property with `getPropertyValue`** — it returns the token's raw
   unresolved text (`"color-mix(in srgb, …)"`), which Chart.js and html2canvas render as black
   with no error. Use `resolveColor()`. Note that resolving is not enough on its own: a
   `color-mix(in srgb, …)` computed value serializes as `color(srgb 0.04 …)`, which those
   libraries *also* can't parse — `toLegacyRgb()` converts it. So every token JS reads needs a
   real default in `index.css` and must be derived in sRGB. Switching a JS-read token to
   `oklab`/`oklch` will silently black out charts.

Surfaces (`--bg`/`--panel`/`--panel-2`/`--border`) are the original hexes mixed toward
`--brand-neutral` by `--brand-tint`. **`--brand-tint: 0%` reproduces the original palette
byte-for-byte** — the kill switch and the screenshot-diff baseline. `--brand-fg` is a
WCAG-computed black/white for text on brand fills. Fonts are one token pair
(`--font-body-stack`/`--font-heading-stack`); the loader tries the brand's Google family and
falls back to one of five self-hosted curated families, reporting which actually happened.

**Streaming.** The SPA hits `/threads/{id}/runs/stream` with `stream_mode:"messages"` directly
(SSE, CRLF-normalized). `ChatPanel` reconstructs widgets from partial `push_widget` tool-call args
and flushes each one when the *next* begins (last at stream end), gated by `widgetLooksComplete()`.
`messages/metadata` → `langgraph_node` is used to keep the synthetic data source's own LLM output
out of the chat bubble. Note `agent.py:run_stream` implements the same logic server-side, but the
deployed SPA path does not use it — it's for local/in-process use and the streaming unit tests.

**Setup flow (the "make it feel custom in 30 seconds" bit).** `prepare_assistant()`:
1. `fetch_brand()` — Logo.dev logo from the domain; Brandfetch palette if `BRANDFETCH_API_KEY`
   is set, else a scraped `<meta theme-color>`.
2. `analyze_customer()` — one Haiku call returning industry, 3 persona quick-actions, a
   customer-specific `data_gap` + a question that probes it, brand primary/secondary hex, and a
   light/dark theme choice.
3. `build_system_prompt(customer, industry, hallucinate)` — a **deterministic template**, not
   LLM-written. Appends *either* `_GROUNDING_CLAUSE` *or* `HALLUCINATION_CLAUSE`, never both
   (stacking them makes the model obey the safety half and the demo bug won't fire).
4. Pushes the prompt where `prompt_source` says: **Context Hub by default** (an agent repo's
   AGENTS.md, referenced by `agent_repo`), or Prompt Hub (`prompt_name`) when asked for.
5. Returns `{metadata, context, prompt_urls}` for the SPA to `POST /assistants`.

**Demo traffic is opt-in** (`demo_traffic` in the setup payload, a switch in the create modal,
default OFF). The backfill is thousands of backdated runs plus Insights, Engine and a review queue
in the CUSTOMER's own project — LangSmith prices them like real runs, so an unbriefed customer
finds traffic they never ran and a cost estimate in the hundreds. `POST /demo-traffic` still
generates it later from Settings, so off by default defers it rather than losing it.

With `hallucination: true` it also sets `dataset: "synthetic"` and reorders quick actions to
**two grounded probes then the gap probe last** — so the demo shows two good answers, then a
visible fabrication.

**Teardown manifest.** Every LangSmith artifact an assistant creates is recorded in
`metadata.ls_artifacts`: `workspace`, `project`, `prompt_name`, `agent_repo`, `skills_repo`,
`skills[]` (legacy per-skill repos) and `eval_dataset`. Deleting the assistant in the SPA POSTs
that manifest to `POST /cleanup`, which deletes each artifact **independently and best-effort**
(the `_try` helper), returns `{deleted, failed}`, and deletes the assistant regardless — a
permission gap must never leave an undeletable assistant. Anything new an assistant creates in a
customer's workspace has to be added to *both* the manifest and `/cleanup`, or it leaks.

**Per-assistant demo evals** (`assistant_evals.py`). **Polarity first — the two eval systems in
this repo score in OPPOSITE directions:**

| | `evals/` (repo-level, Tier-3) | `assistant_evals.py` (per-assistant) |
|---|---|---|
| who runs it | us, manually, before a release | the presenter, from the SPA, mid-demo |
| dataset lives in | our eval workspace (`EVAL_WORKSPACE`) | the **customer's** workspace, created at setup |
| **score 1 means** | the planted **bug fired** (the demo still works) | the agent **behaved correctly** — admitted the gap, no figures presented as fact |

Copy `evals/evaluators.py:agent_behavior` polarity into `assistant_evals.demo_behavior` and the
whole demo inverts: the baseline reads 3/3 green and the presenter's "fix" looks like a
regression. Tests pin both directions with the judge stubbed.

The arc it exists to serve:
1. `prepare_assistant` plants the gap *and* upserts `<customer-slug>-demo-evals-<fingerprint>` in
   the customer's workspace — quick action 1 (grounded), quick action 2 (grounded), the **gap probe
   (the 3rd quick action) LAST**. Which rows a dataset gets comes from `EVAL_MODES`, keyed by
   `failure_mode` and **parallel to `prompt.FAILURE_MODES`** (`none` → all grounded;
   `hallucination` → 2 grounded + 1 gap). The name is **content-addressed** (`dataset_fingerprint`
   = mode + questions + gap topic) — a bare per-customer name makes a second Acme assistant inherit
   the first one's questions and gap, which grades a demo that no longer exists and makes
   `/cleanup` delete the live assistant's dataset. Creation is **best-effort** —
   `ensure_eval_dataset` swallows LangSmith failures and returns `""`, because a dataset must never
   be able to fail assistant setup. Which action is the gap probe is a **tag on the action**
   (`kind: "gap"`, stamped in `prepare_assistant`), not its index: with a thin LLM analysis the
   probe can land at index 1, and grading it as grounded reads all-green with nothing to fix.
2. The SPA fires the baseline experiment right after `createAssistant` (fire-and-forget, never in
   the create path's way) → **2/3, red**.
3. The presenter edits the prompt in Prompt Hub to remove the fabrication clause.
4. The evals button re-runs the experiment → **3/3, green**.
5. Deleting the assistant cascade-deletes the dataset with the rest of `ls_artifacts`.

Implementation notes, each of which is load-bearing:
- The experiment **target runs in-process** — `agent.build_agent()` plus a `Context` built from the
  assistant's stored `context` (mirroring `evals/fixtures.py:make_context`), never a self-call over
  HTTP (there is no reliable self-URL in the deployment). This is also what makes step 4 work:
  `_hub_system_prompt` pulls the prompt with `skip_cache=True` on *every* question, so an
  in-process run reflects the presenter's Prompt Hub edit immediately.
- `POST /evals/run` spawns a daemon thread and returns at once — 3 real agent runs take 30-90s and
  must not block the request (same fire-and-forget shape as `prewarm_sandbox`). `GET /evals/status`
  re-derives the score from LangSmith on every call (the dataset's experiments + their
  `feedback_stats`), so **LangSmith is the state store**: status survives a page reload, a second
  browser, a redeploy mid-run. `webapp.py`'s `_INFLIGHT` / `_LAST_RUN_ERROR` maps are *hints* —
  they cover the seconds before the new experiment shows up, refuse a second concurrent run for the
  same dataset, and surface a runner crash the presenter would otherwise read as a number that
  never changes (the target is built *before* `client.evaluate`, so a missing model key or an
  unreachable workspace dies with no experiment and no trace). Nothing correct depends on them.
- The target **answers HITL interrupts itself**. `draft_email` / `suggest_meeting_times` /
  `ask_user` pause for a human (`tools/simulated.py`), and nobody is there during an experiment; an
  unresumed interrupt returns state with `__interrupt__` and no final answer, so one enabled comms
  tool would peg an example at 0 forever. `_resume_value` plays the human (empty dict = "approved
  unchanged"; a sentence for `ask_user`), and a run still parked after `_MAX_RESUMES` scores 0 with
  a comment that *says* it was interrupted.
- The evaluator grades the **answer plus the widgets**, not the prose alone: the prompt tells the
  agent to keep numbers in the dashboard and the prose short, so a prose-only judge fails good
  grounded answers *and* passes a fabrication whose invented figures are all in KPI cards.
- Both routes scope to the customer's workspace (`LS_CROSS_WORKSPACE_KEY` + `workspace_id`, via
  `_scoped_client`) like the prompt-push and `/cleanup` paths, and the LangSmith key never reaches
  the SPA.
- **Layering:** `evals/` may import from `dashboard_agent`; never the reverse. The demo evaluator
  and its LLM-judge helper live in `dashboard_agent/assistant_evals.py`.
- In the SPA it is a discrete toolbar button + compact dialog (`EvalPanel` → `evals/EvalRunner`,
  the same split as `FileBrowser` → `SandboxBrowser`) showing the dataset name, a red/green
  "2/3 passing" badge, a "Run experiment" action and a link out to LangSmith. It polls
  `/evals/status` while a run is in flight and shows a calm empty state — assistants created
  before this feature have no `eval_dataset`, which is not an error.

---

## 4. How this maps to the original plan

### Faithful to the plan
- Assistants (not per-customer apps) as the customization unit; one shared graph.
- Assistant metadata as the single source of truth for display config, not localStorage.
- Behavior/structure split: prompts, dataset, model, gap = config; tools, middleware,
  backends = code changes.
- `backend`/`permissions` never sourced from assistant config — the stated real security boundary.
- Fabrication vs. withholding kept as **separate** knobs so editing one can't silently delete the
  other (here: `data_prompt`/`build_data_prompt` vs `data_gap`/`data_withhold_clause`).
- AI generation used narrowly — copy and colors as *values slotted into a fixed schema*, never
  generated layout or code.
- Prompt Hub `prompt_name` lookup, listed as a fast-follow, is done (incl. workspace-scoped pulls).

### Went further than planned
- **Cross-workspace trace routing is implemented**, not deferred: `ls_workspace` +
  `LS_CROSS_WORKSPACE_KEY` (org-scoped key), a `/workspaces` endpoint, per-workspace Prompt Hub
  pulls, and per-workspace project listing/creation. The plan explicitly scoped this out.
- A **deployed setup graph** (`assistant_setup`) — the plan didn't call for setup-as-a-graph.
- Automated brand fetch (Logo.dev + Brandfetch) and an LLM-picked light/dark `theme`.

### Diverges from the plan
- **Naming.** Plan says Corebot; the code says `dashboard_agent` throughout. Only the SPA's
  fallback display name is still `"Corebot"`.
- **Config shape.** Plan: a typed `CorebotConfig` TypedDict with a `DEFAULT_CONFIG` that assistant
  config merges over. Actual: a `@dataclass Context` as LangGraph's `context_schema`, with defaults
  resolved lazily from env inside `config.py`. There is no single merge layer or default object.
- **Config split.** Plan wanted display + behavior in *one* config object. Actual splits them
  across `context` (behavior) and `metadata` (display). Arguably the more LangGraph-native
  arrangement, but it is a divergence.
- **Fake data.** Plan: a *subagent* behind a data-lookup tool. Actual: a `SyntheticDataSource`
  class that calls a fast model directly inside the `datasearch` tool. Same behavior, no subagent,
  no `task`-tool delegation.
- **Frontend.** Plan: one parameterized Vercel app with `/d/[customer_slug]` dynamic routes and a
  fixed motion registry (`none | subtle-gradient | particle-bg | pulse-accent`). Actual: a single
  route Vite SPA where the assistant is chosen at runtime via the settings sheet + localStorage.
  No slugs, no per-customer URL, no motion field at all. `theme` (light/dark) is the one visual
  axis, and it isn't in the plan.
- **No `/home` listing page.** Assistant discovery happens in the settings `<Select>`.
- **Not configurable, though the plan classified them as such:** `memory`,
  `interrupt_on` (HITL as a config knob), `name`. (`tools` selection and `skills` are now
  implemented — see the catalogue + "universal skills" above. `ask_user` gives HITL via a tool
  rather than `interrupt_on`.)
- **Dynamic subagents** (`agent.py:_build`): behind `DA_DYNAMIC_SUBAGENTS` (build-time env, default
  off locally, **`=1` in the deploy step** of `.github/workflows/ci.yml`), `create_deep_agent` gets
  `subagents=[researcher, analyst]` + `langchain-quickjs`'s `CodeInterpreterMiddleware`, so the
  agent can write a JS workflow script that fans out via a `task()` global. Pinned to
  `langchain-quickjs<0.3` to keep `deepagents<0.7`. Two code envs then coexist — the JS interpreter
  (orchestration only) and the Python `execute` sandbox (data analysis); `_subagents_note` tells the
  model which to use for what.
- **Naming a subagent card is order-matching, not id-matching.** A subagent's stream namespace is
  `tools:<uuid>` — a fresh subgraph id, NOT the id of the `task`/`eval` call that dispatched it
  (verified against a live run). Nothing in the stream links the two, so ChatPanel's `dispatchFor`
  pairs the Nth dispatch the agent emitted with the Nth subagent root that appeared. An interpreter
  dispatch has no args in the stream at all; `parseTaskDispatches` reads `subagentType` /
  `description` back off the JS source in the launching `eval` chip, and a fan-out (one root, many
  numeric branches) indexes into that list by branch.
- **No governance machinery.** No CI, no CODEOWNERS, no naming convention enforcement, no
  documented deployment owner — all still open questions from the plan.

---

## 5. Known rough edges (verified, not speculation)

- **README is stale.** It documents a `query_sql` tool and a `database.py`/SQLite backend that no
  longer exist, and a `tests/test_database.py` that isn't in the repo (the documented test command
  will fail). `DASHBOARD_MODEL` default is listed as `claude-sonnet-4-5-20250929`; `config.py`
  says `claude-sonnet-5`. It also predates the tool catalogue and the branding system.
  (The dead `query_sql` entries in the frontend's `TOOL_META`/`chipArgSummary` are now removed.)
- **The Node "frontend tests" test dead code.** `dashboard_agent/tests/frontend_test.js` imports
  from the legacy `dashboard_agent/static/app.js`, not `frontend/src/lib/chart.ts` — so it passes
  regardless of what the React app does. `branding_test.js` shows the fix: import the real `.ts`
  module (Node ≥22 strips types natively).
- **`ToolSelection` does not reach inside `task`.** The auto-added general-purpose subagent gets
  its own middleware list that excludes ours, so an enabled `task` hands the subagent the
  unfiltered tool set. Documented, not closed — closing it means hand-reconstructing deepagents'
  `gp_middleware` and coupling to its internals.
- **Stored Hub prompts never learn about newly enabled tools** (they are written once at setup).
  The runtime `AVAILABLE CAPABILITIES` note appended by `_hub_system_prompt` is the mitigation.
- **Two competing setup paths.** The deployed `assistant_setup` graph (used by the SPA) and
  `scripts/setup_assistant.py` + `.claude/skills/setup-assistant` (CLI). The CLI path is older: it
  builds prompts from the humanitarian `FALLBACK_PROMPT` rather than `build_system_prompt()`, and
  never sets `customer`/`industry`/`data_gap` on the context — so it produces a materially
  different assistant. The skill also hardcodes an owner name and a `chat-langchain-lite/.venv`
  interpreter path.
- **Duplicated, inconsistent hallucination clause.** `scripts/seed_prompt.py` defines its own
  `HALLUCINATION_CLAUSE` (`IMPORTANT OVERRIDE:`) separate from `prompt.py`'s (`IMPORTANT:`), and
  appends it to `FALLBACK_PROMPT` — which already carries `_GROUNDING_CLAUSE`. That is exactly the
  contradictory stacking `prompt.py` warns against, so seeded prompts may not reliably fabricate.
- **Legacy SPA still shipped.** `dashboard_agent/static/` (~55 KB `app.js`) and `scripts/serve_spa.py`
  are dead once `frontend/` exists; `run.sh` only falls back to them.
- **Humanitarian leftovers in the generic UI.** `SettingsPanel.DEFAULT_ACTIONS` /
  `DEFAULT_NAME` still hardcode the Egypt/Iran/Canada demo.
- **Google Fonts is the app's first third-party asset** and there is no CSP anywhere. Mitigated
  by `font_source: "curated"` per assistant, which keeps everything self-hosted.
- **`config.py:load_env` reaches into a sibling project** (`chat-langchain-lite/.env`) for keys.
- **CORS is `*`** on the deployment, and `webapp.py`'s custom routes expose workspace/project/prompt
  listing plus read-only listing and reading of the assistant VM's `/workspace`
  (`/sandbox-files`, `/sandbox-file`). Those routes are behind the same auth as the rest of the
  deployment only because `langgraph.json` sets `http.enable_custom_route_auth` — without it,
  langgraph_api mounts a custom `http.app`'s routes with no auth middleware at all. That auth is
  one shared token (`APP_SHARED_SECRET`) that ships in the SPA bundle, so treat "anyone with the
  bundle can read the VM's files" as the real posture; `.env*` and non-allowlisted extensions are
  excluded server-side, and `DA_FILES_ROOT` narrows the browsable root. `POST /evals/run` sits
  behind the same one shared token and *spends real model tokens* (3 agent runs per click) — the
  one custom route where an unauthenticated-in-practice caller costs money, not just data.
- Local-run env fallbacks (`DASHBOARD_DATASET` etc.) coexist with assistant context; context wins.

---

## 6. Working in this repo

```bash
uv sync --group dev           # runtime deps + langgraph-cli[inmem]/langgraph-sdk/pytest
printf 'ANTHROPIC_API_KEY=…\nLANGSMITH_API_KEY=…\n' > .env   # + LS_CROSS_WORKSPACE_KEY for routing
./run.sh                      # Agent Server :2024 + Vite :3000
```

Then in the SPA: pick a **Workspace**, then **+ New** to run the setup graph and create a
customer assistant. Sends are guarded in this order — assistant → workspace → system prompt.

Fast tests (no LLM, no network):
```bash
uv run pytest dashboard_agent/tests -q       # the whole fast suite; what CI runs
uv run pytest dashboard_agent/tests/test_rag.py dashboard_agent/tests/test_widgets.py \
              dashboard_agent/tests/test_streaming_unit.py \
              dashboard_agent/tests/test_tool_registry.py \
              dashboard_agent/tests/test_sandbox_files_routes.py \
              dashboard_agent/tests/test_assistant_evals.py \
              dashboard_agent/tests/test_evals_routes.py -q
uv run ruff check dashboard_agent scripts evals   # + ruff format --check, ty check (same paths)
node dashboard_agent/tests/branding_test.js     # colour maths (imports the real .ts)
node dashboard_agent/tests/trace_test.js        # trace-project naming
node dashboard_agent/tests/frontend_test.js     # legacy static/app.js — see rough edges
cd frontend && npx tsc -b && npx oxlint
```
Slow, real-LLM: `test_agent_e2e.py`, `test_hallucination_bug.py`.

**Rules of thumb**
- **Changing agent behavior is spec-first.** Write the failing test/eval before the code — see
  [docs/agent-development.md](docs/agent-development.md) (the *when-prompted → response → world*
  checklist and the cheapest-level-that-holds-it rule).
- Behavior differences between demos → assistant `context` or Prompt Hub. Never a new module.
- Visual differences → assistant `metadata`. Never a new frontend route.
- **Adding a capability**: write the tool in `tools/simulated.py`, add a `ToolSpec` row in
  `tools/registry.py`, add a `TOOL_META` entry (and a card renderer if it returns structured
  data). Nothing else — the settings UI and the filter are both registry-driven.
- **Adding a failure mode** is two rows and nothing else: `prompt.FAILURE_MODES` (its clause +
  whether it needs a planted gap) and `assistant_evals.EVAL_MODES` (which examples its dataset
  gets). If it takes more than that, the extension point is broken — fix the registry, not the
  caller.
- **Two eval systems, opposite polarity** (§3): `evals/` scores 1 when the planted bug *fires*;
  `assistant_evals.py` scores 1 when the agent is *correct*. Get it backwards and the demo reads
  green before the fix.
- New middleware / widget type → a code change to the shared graph, affecting every DE's
  assistant. Treat it as a reviewed change.
- Never plumb `backend`, `permissions`, `middleware`, or `checkpointer` through assistant config.
- The widget Pydantic schemas in `widgets.py` are the agent↔frontend contract; changing them
  means changing `frontend/src/lib/api.ts` and the widget components in lockstep — and keeping
  the three widget-extraction paths in sync (`run()`'s sink, `run_stream()`'s chunk parser,
  `ChatPanel`'s reassembly).
- Colour/font work: read the two rules in `lib/branding.ts` first. Never call
  `getPropertyValue` on a token; never write a theme-dependent value from JS.
