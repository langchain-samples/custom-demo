# Customer-demo workbench: implementation guide

This repository prepares and presents customer-specific agent demos. Dashboards, HTML artifacts,
approvals, MCP Apps and voice are presentation capabilities, not separate products. The lifecycle
is **prepare → publish → present → inspect/improve → retire**.

This file describes implementation ownership and invariants. [README.md](README.md) is the
presenter guide; [CLAUDE.md](CLAUDE.md) owns development conventions and CI checks. This root
`AGENTS.md` is not a model prompt: those live in the assistant's Context Hub agent repository.

## 1. Owners, lifetimes and dependency direction

One shared LangGraph deployment and one React SPA serve every customer. Differences between
demos belong in assistant configuration and referenced resources, not new graphs or forks.

| Concept | Owner | Lifetime |
|---|---|---|
| Resolved scenario | `custom_demo/core/demo.py:DemoPlan` | One preparation; shared inputs for provisioning, evals, traffic and brief |
| Named resource handles | `custom_demo/core/demo.py:LsArtifacts` | Serialized with the assistant; cleanup targets, not exclusive ownership |
| Saved assistant | LangGraph assistant API | Persistent execution `context` and display/demo `metadata` |
| Presenter session | App's `useAssistantSession` | Selection, editable draft, immediate previews and temporary overrides |
| Acknowledged assistant state | React Query | Cached server records, updated after successful writes |
| Conversation | LangGraph thread and ChatPanel | Messages, interrupts, goal and streamed output |
| Working files | `custom_demo/resources/sandbox.py` | Assistant VM, independent of individual conversations |
| Execution topology | `custom_demo/runtime/backends.py` | Filesystems resolved from each current run |
| Compiled graph | `custom_demo/runtime/agent.py` | Shared execution engine, not an assistant-resource container |

Dependency direction:
- `core/` contains domain/configuration records, not orchestration or SDK clients.
- `resources/` owns sandbox identity, seeding and acquisition without importing the graph or
  accepting LangGraph runtime objects. Setup, runtime adapters and file routes are peer consumers.
- `runtime/backends.py` adapts `Context` to resource-backed filesystems; `runtime/agent.py`
  assembles models, middleware and tools.
- `provisioning/` plans/prepares scenarios and runs evals/traffic; runtime does not import it.
- `web/` contains HTTP adapters. Importing a handler must not eagerly assemble all routes.
- `evals/` may import `custom_demo`, never the reverse.
- App owns assistant-session state; Settings edits it. Execution must not depend on an
  imperative handle into a settings view.

Backend-relative paths below are under `custom_demo/`; frontend paths name their full repo path.

## 2. Repository map

```text
custom_demo/
  core/ctx.py                Pydantic assistant configuration supplied per run
  core/demo.py               DemoPlan and LsArtifacts
  resources/sandbox.py       VM identity, credentials, seed scripts, lifecycle/cache
  runtime/backends.py        DynamicBackend and Context Hub filesystem routing
  runtime/agent.py           Shared model/middleware/agent construction
  runtime/prompt.py          Prompt templates and fresh Context Hub reads
  runtime/tools/registry.py  Catalogue, guidance, selection and call caps
  runtime/tools/core.py      push_widget and invocation-local widget collection
  runtime/tools/simulated.py Draft generation and human interrupts
  runtime/tools/web_search.py Real Tavily results
  runtime/mcp_servers.py     Remote discovery, adaptation, cache and app resources
  runtime/widgets.py         Validated widget contract
  runtime/mocking.py         Per-invocation tool mocking
  provisioning/setup.py     Discovery, pure planning and resource preparation
  provisioning/evals.py     Presenter-facing datasets, judges and experiment runs
  provisioning/traffic.py   Optional synthetic traffic and review queue
  provisioning/resource_tags.py  Application tagging
  web/routes.py             HTTP route assembly
  web/                      Metadata, cleanup, sandbox, MCP, voice and eval handlers
  voice/                    Voice token minting and trace support
  config.py                 Environment, model and scoped-client configuration
  graph.py                  dashboard_agent entrypoint and trace routing
  setup_graph.py            assistant_setup entrypoint
  auth.py                   Shared-token deployment authentication
  webapp.py                 HTTP deployment entrypoint
  tests/                    Unit, contract, integration and explicit live tests
frontend/src/
  App.tsx                   Presenter-session owner and output layout
  lib/assistantSession.ts   Draft, preview and run-context projections
  lib/assistantEdits.ts     Per-assistant serialized writes
  lib/assistantLifecycle.ts Prepare/publish/baseline and cleanup/delete sequences
  lib/hooks/useAssistantSession.ts    Selection, edits and readiness
  lib/hooks/useAssistantAppearance.ts Brand and typography effects
  components/SettingsPanel.tsx        Settings view and dialogs
  components/ChatPanel.tsx            Conversation and stream reconstruction
  components/chat/toolActivity.ts     Ordered main/subagent tool activity
  lib/api.ts                HTTP transport and frontend wire types
  lib/queries.ts            React Query reads and cache keys
mcp_demo_server/             Fieldlink Logistics and Meridian Wealth examples
scripts/                    Local runners, connectivity and repository checks
```

`langgraph.json` declares the entrypoints. Python is constrained to 3.13 to match the deployment
image and managed with uv; frontend dependencies use npm. The `deepagents` 0.7 and
`langchain-quickjs` 0.3 dependency ranges are coupled. CI and fixed identifiers are in CLAUDE.md.

## 3. Prepare, publish and retire

`prepare_assistant` runs brand discovery and customer analysis concurrently, copying the tracing
context into each worker. Analysis proposes customer-specific skills, starting files, persona
questions, capabilities and a data gap; brand discovery contributes visual values.

`plan_demo` resolves them into `DemoPlan` without clients, network calls or background tasks.
Missing starting data or an unusable analysis fails before resource creation. Plan collections
retain caller/provider fields rather than creating a second validation schema. Downstream
consumers treat them as read-only; the frozen record is not deeply immutable.

Preparation then follows an explicit order:
1. Push the skills bundle when requested.
2. Build and push the deterministic system prompt. Append the extra skills clause only when
   bundle publication returned a handle, not merely because a bundle was planned.
3. Start VM prewarming with the plan's unique sandbox key and seed specification.
4. Attempt eval dataset and evaluator provisioning.
5. Start best-effort resource tagging and, only when opted in, demo traffic.
6. Return the existing metadata/context/prompt-URL payload and presenter brief.

The same finalized actions, tools, seeds and gap feed the applicable consumers. Do not rebuild
example questions independently inside evals or traffic. Generated skills carry both a workflow
and sandbox step; missing/unknown workflow uses the default pattern. Keep skill questions and
referenced seed filenames consistent. Grounded actions lead, and a generated gap probe carries
`kind: "gap"`; identify it by that tag rather than assuming it is always the third action.

`frontend/src/lib/assistantLifecycle.ts` separately publishes the assistant record and starts
its baseline experiment. Preparation/publication are not a transaction. A partial preparation
or failed publication has no compensating rollback.

### Resource handles are not an ownership ledger

`LsArtifacts` serializes ten fields: `workspace`, `project`, `agent_repo`, `skills_repo`, `skills`,
`eval_dataset`, `eval_rule_id`, `eval_evaluator_id`, `eval_judge_prompt`, `annotation_queue`.
Its tagging projection derives supported targets from those same handles.

Customer-derived prompt/skill names may be shared; datasets are content-addressed by scenario.
Project/queue names may precede creation. A judge prompt created before a failed evaluator
attachment may not be recorded. Deleting shared resources can affect another assistant.

`web/cleanup.py` uses a non-coercing adapter for stored manifests; null, empty and omitted
values retain their compatibility behavior. One worker thread runs the sequential cascade and
collects per-artifact failures. Preserve rule-before-evaluator-before-judge-prompt ordering,
legacy individual skill deletion, and skills-bundle deletion as an agent repo. The UI reports
artifact failures and proceeds to assistant deletion. VMs expire by retention policy, not this
manifest. New resource types require coordinated domain, provisioning, cleanup, frontend and
test changes; do not record successful ownership where only an intended name is known.

## 4. Presenter session versus saved assistant

`useAssistantSession` owns selected identity, workspace preference, saved-assistant lookup,
draft, derived display assistant, readiness and edit commands. App uses it directly. Settings
owns view concerns such as dialogs, recovery presentation and resizing.

`assistantSession.ts` separates saved configuration from preview. Branding/model/tools/MCP
edits apply immediately to the draft. `AssistantEdits` debounces channels and serializes writes
per assistant, deriving each PATCH from the latest acknowledged cache record. PATCH replaces
whole `context`/`metadata` objects: preserve unknown fields and nested voice metadata. Resetting
a model removes its key. Empty tools and absent tools differ. Failed saves leave local previews
but do not update acknowledged state. Branding/model/tools debounce at 600ms; MCP at 800ms.

The prompt-repo dropdown is a **temporary override**. Chat uses it; evals use saved assistant
context. Workspace preference is also distinct from ownership: explicitly selecting an
incompatible workspace clears the assistant, but restoration retains the browser's workspace.
Changing either policy is a product decision, not an incidental refactor.

Creation and retirement sequences are testable outside Settings. Conversation reset belongs
to App/ChatPanel. The transport's thread cache and some delayed UI completions still lack full
generation scoping; the per-assistant write queue does not make all switching races impossible.

## 5. Runtime, configuration and assistant resources

### Middleware order

The build order in `runtime/agent.py` is load-bearing:

| Order | Middleware | Invariant |
|---|---|---|
| 1 | `RubricMiddleware` | Required at build, inert without a rubric; its after-hook must run last |
| 2 | `ConfigurableModel` | Apply the run's model choice |
| 3 | `McpTools` | Discover tools before the prompt describes them |
| 4 | `_hub_system_prompt` | Fetch the configured prompt per model call and append runtime notes |
| 5 | Catalogue call limits | Apply each enabled tool's caps |
| 6 | Optional QuickJS interpreter | Add orchestration before final filtering |
| 7 | `ToolSelection` | Final say on tools offered to the model |

After-hooks run in reverse. Both sync and async hooks must remain callable. MCP's sync hooks
pass through without discovery; synchronous in-process eval/traffic runs do not acquire remote
MCP tools through that middleware. The async tool-call hook must supply adapted tool objects
as well as the model-call hook advertising them.

No `write_todos` is installed. `DYNAMIC_SUBAGENTS` enables QuickJS orchestration and named
specialists; an explicitly enabled but unbuildable interpreter raises. Python `execute` does
data work, while QuickJS orchestrates task calls. Do not infer production flags from CI/local
defaults or change prompts to advertise tools that the installed graph does not provide.

### Configuration and prompt sources

`core/ctx.py:Context` validates assistant configuration at the run boundary. It includes model,
prompt/skills repo references, customer, industry, workspace, tools, seed files, sandbox key and
MCP connections. The graph factory also reads `ls_project` for tracing. Do not expose backend,
permissions, middleware or checkpointer implementations as assistant-configurable values.

The named Context Hub repo's `AGENTS.md` is the prompt source. A failed named repo raises
`PromptSourceError`; `FALLBACK_PROMPT` is for no repo, not an outage. Framework skills/filesystem
instructions are composed when `skills_repo` or `agent_repo` is present. Prompt strings and
`@tool` docstrings affect model behavior; they are not ordinary documentation comments.

Trace routing uses `ls_workspace` and `ls_project`. Prepared assistants use the customer name
as their trace project; the frontend prefers an explicit project, then customer/name/ID. The
setup graph's own traces use `SETUP_TRACE_PROJECT`, default `custom-demos`. Agent Server traces
remain independent roots; voice links to the agent run ID instead of forcing parentage.

### Acquisition versus filesystem topology

`resources/sandbox.py` accepts ordinary identity and seed values:
- `sandbox_key_from` prefers explicit `sandbox_key`, then legacy agent repo/customer/default.
- `ensure_sandbox` may create and seed; `attach_sandbox` cannot create or seed, but may restart
  an existing stopped VM. `prewarm_sandbox` is a best-effort setup consumer.
- Production callers check effective sandbox enablement before acquisition. These APIs express
  acquisition policy, not self-enforcing authorization.
- Credentials and scope headers come from the deployment, **not** the assistant's trace/Hub
  workspace. Do not merge those two scopes.

The cache stores backend and validation timestamp together. Preserve `da-` VM names, 600-second
revalidation, 3,600-second idle TTL and seven-day stopped retention. Seed only a new VM. Runtime
validates the seed before acquisition on every turn; `SeedSpecError` must not become unrelated
fallback data. Ordinary acquisition failures can fall back to StateBackend. Seed filenames and
payloads are sanitized; model-authored values must not be interpolated as shell commands.

`runtime/backends.py:DynamicBackend` is one shared adapter that resolves the current run on
every access, not at graph build. `execute` is offered only when its actual default is a sandbox.
The skills bundle mounts at `/skills/`; routing strips that prefix, so bundle files live at
`<skill>/SKILL.md` at the repo root. Hub construction errors raise `BackendSourceError`; later
Hub I/O errors surface during access. Off-run resolution uses StateBackend.

An assistant with `agent_repo` but no `skills_repo` uses the whole Hub repo as default and does
not acquire a sandbox. Preserve that compatibility branch until a migration is designed.
File browsing, media reads and uploads use attach-only acquisition; no browser operation may
provision a VM. Path/extension policies and sensitive-name exclusions live in `web/sandbox.py`;
the allowed root defaults to `/workspace`. Uploads are writes, not a read-only capability.

## 6. Execution and presentation contracts

`runtime/tools/registry.py` is authoritative for catalogue selection, UI labels and call caps.
`push_widget` is default-on but optional; `ask_user` is always-on and capped. `draft_email`
creates an approved draft, not a delivery. `web_search` uses real Tavily results.

Unset runtime selection uses defaults; `[]` leaves only always-on tools. Setup separately
unions picks with defaults and retains empty-input truthiness. Names outside the catalogue
pass through, including built-ins and namespaced MCP tools. The auto-added general-purpose
subagent does not inherit this app's selection middleware; catalogue selection is not a full
subagent permission boundary.

Widget schemas in `runtime/widgets.py` and frontend API types/renderers must agree. ChatPanel
alone reconstructs partial widget arguments and flushes complete widgets as the stream advances.
Server-side consumers collect executed widgets through `widget_sink`. Do not add a server-side
copy of the partial-stream parser.

`ToolActivity` keeps main and subagent stores separate. Only real tool-call IDs create chips;
preserve first-seen order/progress, main-only code previews, results and pending resume/freeze
behavior. Subagent namespaces are generated independently of dispatch IDs: label cards by
dispatch order, then fan-out branch. Interpreter task descriptions come from the launching code.

HTML artifacts are agent-written files, with streamed previews followed by sandbox reads.
Voice drives the same browser streaming path rather than a second server-side execution path;
see [docs/voice-mode.md](docs/voice-mode.md).

Human approval pauses with `interrupt()`. Resume sends `command` instead of new `input`.
Nodes restart from the top, so draft generation caches pending output by tool-call ID.
`/goal` both sets a goal and runs its objective. Send its rubric every turn, empty when cleared:
omission preserves checkpointed values. Status comes from custom events, and middleware message
namespaces must be filtered so grader JSON is not rendered as the answer. Stream modes include
messages, updates and custom.

## 7. MCP protocol and browser boundaries

Remote URLs are outbound from the deployment; local servers need a public tunnel for deployed
agents. Tools use `{server_id}_{tool}` names to avoid catalogue collisions. Discovery and
adapted-tool caches include connection configuration. Failed discovery temporarily removes the
server's tools instead of failing the turn; diagnose it through the Settings probe.

The stateless demo servers use guard-based `InputRequiredResult`, not server-push `ctx.elicit()`.
Do no irreversible work before the guard: the client re-calls the tool with answers. Resume
responses must be keyed by the server's request key. Elicitation values must be primitives;
nested objects are invalid. Schema render context belongs on a property because root-level
custom fields are normalized away by the SDK.

MCP App `ui://` HTML runs in an iframe with `sandbox="allow-scripts"`, never `allow-same-origin`.
Keep message-source validation and the bridge contract: init carries request/theme/accent;
ready, resize, submit and cancel return to the host. Submitted content must match the requested
schema. Signature app tests pin this cross-language contract.

Signature results include text and image content; document images use served URLs rather than
requiring the model to reproduce base64. Drop undersized images that providers reject. URLs
use the request's forwarded host. Free ngrok browser requests can receive an interstitial even
when MCP works; the runner prefers cloudflared when installed. See
[the MCP walkthrough](docs/mcp-apps-with-deep-agents.html) before changing these interfaces.

## 8. Evaluation, evidence and operational limits

| System | Purpose | Score 1 means |
|---|---|---|
| `provisioning/evals.py` | Presenter verifies behavior and live fix | Correct behavior |
| `evals/` | Release verifies the failure remains demonstrable | Planted bug fired |

Keep `runtime/prompt.py:FAILURE_MODES` and `provisioning/evals.py:EVAL_MODES` aligned. Grounding
and fabrication clauses are mutually exclusive. The gap is absent seeded data, not an instruction
to hide a known figure. Use the action's gap tag, not an assumed third position. Faithfully
computed synthetic data remains synthetic, and a file read alone is not proof of grounding:
check claims against the actual file computation, MCP records or web source used.

Presenter evals run in-process with saved assistant context and grade answer plus widgets.
They automatically resume supported human interrupts with caps. LangSmith stores experiments
and feedback; in-flight/error maps in `web/evals.py` are hints, not a durable job store. Dataset
and evaluator preparation are best-effort. Demo traffic remains opt-in and creates billed runs.

Operational limits requiring explicit policy decisions:
- Shared resource names, publication rollback and complete partial-provisioning receipts are
  unresolved lifecycle questions; do not imply per-assistant resource ownership everywhere.
- Temporary prompt/workspace previews can differ from saved eval configuration.
- Thread creation, delayed artifact reads and lifecycle completions are not fully scoped to
  conversation generations; the settings write queue does not fix those separate lifetimes.
- `http.enable_custom_route_auth` protects custom routes with deployment auth, but the token
  is shared and shipped in the SPA. CORS is broad. Bundle access is not tenant isolation;
  sandbox uploads and model/eval/traffic operations add write and cost exposure.
- `/mcp/probe` and `/mcp/app` fetch caller-supplied URLs. Their outbound-request/SSRF exposure
  must be reviewed before widening access; iframe isolation does not protect backend requests.
- Google Fonts loads third-party assets unless curated fonts are selected; no CSP is configured.
  Branding JS writes theme-independent seeds. Resolve computed sRGB through `resolveColor` and
  `toLegacyRgb`, not raw `getPropertyValue`; preserve zero-tint and curated-font fallbacks.
- `config.py` can load a sibling project's environment. SDK startup can attempt network even
  in otherwise mocked tests; explicit network isolation is required for a guaranteed offline run.

## 9. Working and extension points

Follow CLAUDE.md for commands, formatting, error handling, source references and stable names.
For behavior changes, first use [docs/agent-development.md](docs/agent-development.md): specify
when-prompted → response → world and write the cheapest failing test/eval that holds the behavior.

Put scenario policy in planning, resource mechanics in the resource owner, and presentation
controls behind the session/view boundary. New capabilities need a tool implementation, registry
entry and frontend vocabulary/renderer. New failure modes need matching prompt/eval registry
entries. Widget changes require both schema sides and both existing collection paths to agree.

For internal refactors, characterize outputs and side-effect order before moving ownership.
Resource-boundary tests check graph-independent acquisition; setup-policy tests pin prepared
payloads; session tests run without Settings; deferred-write tests pin serialization. Test wire
contracts or behavior rather than incidental source layout. Update this guide when ownership or
lifetime changes, not for every extracted helper.
