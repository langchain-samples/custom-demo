# Custom Demo Agent

A workbench for building and presenting **customer-specific agent demos** without forking an
application. A presenter supplies a customer and use case; setup prepares relevant sample data,
skills, example questions, branding, and evaluation cases. One shared Deep Agent executes the
scenario, and one React app shows its work.

Dashboards are one deliverable, not the product boundary. The same assistant can produce HTML
documents, analyze files, use connected MCP systems, pause for human input, and take spoken questions.

[Watch the walkthrough](https://www.loom.com/share/d5ce4bb5a2b5485baef75d0a1d84f825).

## The demo lifecycle

1. **Prepare.** Choose a workspace, customer, and use case in Settings → **+ New**. Setup
   analyzes the scenario, resolves a demo plan, publishes prompt/skill resources, and starts
   sandbox prewarming. It also attempts to provision the scenario's evaluation dataset.
2. **Publish.** The browser creates a LangGraph assistant from that prepared configuration,
   selects it, and starts a baseline experiment when an eval dataset is available.
3. **Present.** Ask an example question or your own. Inspect streamed tool calls, subagents,
   widgets, files, approvals, and traces. Voice uses the same agent execution path as chat.
4. **Inspect and improve.** Compare claims with their sources, edit the prompt in Context Hub,
   and rerun the questions and evaluations.
5. **Retire.** Delete the assistant in Settings. Cleanup attempts each recorded LangSmith
   resource independently, reports failures, and then removes the assistant record.

Preparation and publication are **not a transaction**. Failed publication does not roll back
prepared resources. Prompt/skill names are customer-derived, and identical eval scenarios can
reuse a dataset. Use distinct customer names for independent demos in the same workspace;
deleting one of two demos with shared resources can affect the other. VM keys are unique per
new assistant, but VMs expire through their own retention policy rather than `/cleanup`.

## Quickstart

You need **uv**, **Python 3.13**, **Node 22+ with npm**, a LangSmith key, and a supported model
provider key (Anthropic by default). Python is constrained to 3.13 to match the deployment
image; uv provisions it from `.python-version`. Node 22 matches CI.

```bash
uv sync --group dev
cp .env.example .env
# Edit .env with your LangSmith and model-provider configuration.
uv run python scripts/preflight.py
./run.sh
```

Preflight makes a model call and a LangSmith request; it is a connectivity check, not an offline
validation. Open <http://127.0.0.1:3000>, choose a workspace in Settings, and create an assistant.

**Rehearse before presenting.** Prewarming runs in the background. VM creation and installing
analysis packages can make the first question slow, and stopped or expired VMs need acquisition
again. Wait for a complete answer and verify the expected files and capabilities before the demo.
Setup requires customer-specific starting files; it does not substitute a generic retail dataset.

### Ways to run

| Mode | Configuration |
|---|---|
| Local UI and backend | `./run.sh`: Agent Server `:2024`, Vite `:3000`; override with `PORT` / `SPA_PORT` |
| Deployed | The configured LangSmith GitHub integration rebuilds the backend on pushes to `main`; Vercel builds the SPA separately |
| Local UI, deployed backend | Configure `VITE_LG_URL` or override it with the `lgUrl` localStorage preference |
| Voice | Use the composer microphone; deployment needs `GEMINI_API_KEY`, not an assistant-level enable flag |

The deployed frontend uses `VITE_LG_URL` and `VITE_LG_API_KEY` at build time. A deployed HTTPS
page cannot ordinarily call a local HTTP backend. Backend deployment secrets belong to the
LangSmith deployment; setting them only in a CI job does not configure that deployment.

## What is saved, and what is only a preview?

A LangGraph **assistant** is a stored configuration of the shared `dashboard_agent` graph:

| State | Owner | Examples |
|---|---|---|
| Execution configuration | Assistant `context` | Model, prompt/skills repo references, enabled tools, seed files, VM key, MCP connections |
| Display and demo configuration | Assistant `metadata` | Branding, quick actions, presenter brief, failure mode, eval/cleanup handles |
| Presenter session | App-level session controller | Selected assistant/workspace, immediate edit previews, temporary prompt choice |
| Conversation | LangGraph thread and chat UI | Messages, interrupts, active goal, streamed outputs |
| Working files | Assistant's sandbox VM | Starting data and generated artifacts across conversations |

Branding, model, tools, and MCP edits apply immediately to the session and are saved with a
short debounce. Saves are serialized per assistant so replacement-object PATCHes do not erase
one another's fields. Failed saves leave the local preview in place, but are not acknowledged
in the saved cache.

**The prompt-repo dropdown is a temporary session override.** It does not save a new
`context.agent_repo`. Evals use saved assistant configuration, not that override. To change the
prompt for both normal runs and evaluations, edit the saved repo's `AGENTS.md` in Context Hub.

Workspace selection is a presenter preference used for browsing and creation. Explicitly
switching to a different workspace clears an incompatible selected assistant; restoring an
assistant does not automatically adopt its workspace. Check both selections before a demo.

### Capabilities

| Capability | Behavior |
|---|---|
| `push_widget` | Streams validated KPI cards, charts, tables, and findings; enabled by default, switchable off |
| `ask_user` | Always available; pauses for a multiple-choice answer, capped per run |
| `draft_email` | Generates a draft and pauses for editing/approval; does not send an email |
| `web_search` | Real Tavily search; reports failure if unavailable rather than fabricating results |
| Filesystem and `task` | Deep Agents built-ins; not part of the optional catalogue |
| `execute` | Available when this run resolves a sandbox-backed default filesystem |
| Remote MCP tools | Discovered from the assistant's configured servers |

At runtime, omitted `enabled_tools` uses defaults and `[]` disables optional catalogue tools;
`ask_user` remains available. Setup has a separate compatibility rule: it unions defaults with
its picks, and an empty caller selection falls through to analysis picks. Use Settings after
creation to disable optional tools.

`context.model` accepts supported `init_chat_model` identifiers. The `langsmith:` prefix uses
the LangSmith model gateway and requires its configured provider and invocation permission.
Changing a default provider may require its integration package as well as its credentials.

## Check the evidence, not just the answer

The seeded files are **synthetic demo data**, not verified customer records. Claims should be
traceable to the source used for that question:

- For local analysis, open the VM files and inspect the rows and computation.
- For an MCP-connected system, inspect the actual tool inputs and returned records.
- For web research, follow the returned URLs and check what they support.
- For generated artifacts, inspect the document itself as well as the chat response.

A preceding `read_file` or `execute` call does **not** prove a later claim is grounded. Compare
the claim with the result. The graph inspector and trace links expose the work; the file browser
shows the current VM contents, which may have changed since a previous turn.

## Demonstrate a failure and fix it live

The optional `hallucination` failure mode instructs the agent to fabricate over a gap in the
seeded data. Setup includes grounded questions followed by a tagged gap probe. The intended arc:

1. Run the grounded questions and verify the answers.
2. Run the gap probe and inspect whether the agent invents the missing figure.
3. Open the saved assistant's `<slug>-agent` repo in **LangSmith Context Hub**, edit `AGENTS.md`,
   remove the **fabricate-over-gaps clause**, and save.
4. Ask again and rerun **Evals**. The prompt is fetched per model call, so no restart is needed.

The intended per-assistant result is **2/3 passing before the fix, 3/3 afterward** when setup
produces the full three-question scenario. This is model behavior to demonstrate and measure,
not a guaranteed score. Missing data, unavailable tools, or an incomplete setup can change it.

There are two evaluation systems with opposite polarity: the presenter-facing eval scores
**correct behavior as 1**; the repository's release evals score **the planted bug firing as 1**.
See [evals/README.md](evals/README.md) before interpreting release results.

## Connect an MCP server

In Settings → **MCP servers**, paste the URL and press **Test**. Successful discovery makes its
tools available to the next asynchronous agent run. A deployed agent connects outbound, so
`localhost` refers to its container, not your laptop. Use a tunnel for a local demo server:

```bash
./scripts/run_mcp_server.sh --tunnel           # Fieldlink Logistics
./scripts/run_mcp_server.sh --wealth --tunnel  # Meridian Wealth
./scripts/run_mcp_server.sh --wealth           # local: http://127.0.0.1:8765/mcp
```

Paste the printed URL **including `/mcp`**, and update it if the tunnel hostname changes.
Protect servers exposed publicly. The examples demonstrate stateless elicitation and MCP Apps:
server-supplied interactive HTML runs in a sandboxed iframe and returns schema-shaped answers.
See [the MCP walkthrough](docs/mcp-apps-with-deep-agents.html) for protocol details.

## Architecture

Keep one deployment and one SPA. Configuration differs per demo; implementations and security
policy remain shared code.

```text
Customer + use case
  -> discovery -> DemoPlan -> provision resources -> prepared payload
  -> browser publishes assistant -> presenter session selects it
  -> shared graph resolves that assistant's resources -> streamed execution
  -> widgets / artifacts / approvals / traces / evaluations
```

| Responsibility | Home |
|---|---|
| Scenario and named resource contract | `custom_demo/core/demo.py`: `DemoPlan`, `LsArtifacts` |
| Discovery and provisioning | `custom_demo/provisioning/setup.py`: `plan_demo`, `prepare_assistant` |
| VM identity, seeding, lifecycle, cache | `custom_demo/resources/sandbox.py` |
| Per-run filesystem topology | `custom_demo/runtime/backends.py`: `DynamicBackend` |
| Agent model, middleware, tools, instructions | `custom_demo/runtime/` |
| HTTP adapters and route table | `custom_demo/web/`, exposed through `custom_demo/webapp.py` |
| Selection, previews, readiness and edits | `frontend/src/lib/hooks/useAssistantSession.ts`, owned by App |
| Serialized saves and publish/retire operations | `frontend/src/lib/assistantEdits.ts`, `frontend/src/lib/assistantLifecycle.ts` |
| Settings view | `frontend/src/components/SettingsPanel.tsx` |
| Conversation stream and output rendering | `frontend/src/components/ChatPanel.tsx` and output components |

Setup and the file browser consume the resource layer directly; neither uses the graph module
as an infrastructure API. Settings edits an app-owned session; closing it does not remove the
application's configuration authority. The plan keeps the scenario's questions, data, tools,
and expected failure behavior consistent across provisioning, presentation, and evaluation.

[AGENTS.md](AGENTS.md) documents implementation boundaries and invariants.
[CLAUDE.md](CLAUDE.md) documents development conventions and checks.

## Configuration and troubleshooting

Use `.env.example` for configuration options. Important switches include `AGENT_MODEL`,
`SANDBOX_ENABLED`, `DYNAMIC_SUBAGENTS`, `GEMINI_API_KEY`, and `TAVILY_API_KEY`.
`LS_CROSS_WORKSPACE_KEY` enables scoped access across LangSmith workspaces. The sandbox uses
**deployment credentials and scope**, not the assistant's trace-workspace selection.

| Symptom | Check |
|---|---|
| Missing data or no `execute` tool | Sandbox flag, credentials, acquisition logs, and the assistant's seed specification |
| Non-Anthropic setup asks for an Anthropic key | Configure `AGENT_MODEL` and the intended provider |
| Azure calls return 404 | Endpoint should not include `/openai/deployments/...` |
| Model rejects `temperature` | Set `MODEL_TEMPERATURE=` to omit it |
| Prompt edit has no effect | Saved assistant repo versus temporary session override, and selected workspace |
| Unexpected model endpoint | Provider base-URL environment overrides; run preflight |
| Missing eval dataset | Provisioning is best-effort; inspect setup logs |

**Deployment posture:** this is a demo system, not a multi-tenant authorization boundary.
The SPA carries a shared app token, custom routes expose workspace operations and sandbox
files/uploads, and model/traffic/eval operations spend real tokens. Keep sensitive data out and
control who can reach the deployment. More detail is in [AGENTS.md](AGENTS.md).

## Validation

```bash
uv run pytest custom_demo/tests evals -q \
  --ignore=custom_demo/tests/test_contexthub_skill.py \
  --ignore=custom_demo/tests/test_hallucination_bug.py
uv run ruff check custom_demo scripts evals mcp_demo_server
uv run ruff format --check custom_demo scripts evals mcp_demo_server
uv run ty check custom_demo scripts evals mcp_demo_server
uv run python scripts/check_blank_after_block.py
uv run python scripts/check_doc_paths.py
npm --prefix frontend run lint
npm --prefix frontend test
npm --prefix frontend run build
for f in custom_demo/tests/*.js; do node "$f" || exit 1; done
```

The excluded tests invoke live models and services. Most other tests use mocks, but SDK client
startup can still attempt network requests; use network isolation when a strictly offline run
is required. Do not infer absence of side effects just from missing tracing output.

Further reading: [agent development](docs/agent-development.md),
[voice mode](docs/voice-mode.md), and [release evaluations](evals/README.md).
