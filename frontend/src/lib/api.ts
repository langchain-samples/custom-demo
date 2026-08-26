/**
 * Typed client for the LangGraph Agent Server, mirroring every endpoint used by
 * the original static/app.js. Base URL, headers and the assistant id come from
 * ./config. CORS on the server is "*", so these are plain fetch calls.
 *
 * Endpoints covered:
 *   POST   /threads
 *   GET    /threads/{tid}/state
 *   POST   /threads/{tid}/runs/stream   (SSE, stream_mode:"messages", CRLF framing)
 *   POST   /threads/{tid}/runs/wait     (assistant_setup graph)
 *   POST   /assistants/search
 *   GET    /assistants/{id}
 *   POST   /assistants
 *   PATCH  /assistants/{id}
 *   DELETE /assistants/{id}
 *   GET    /workspaces
 *   GET    /projects  ·  POST /projects
 *   GET    /hub-prompts
 *   GET    /sandbox-files  ·  GET /sandbox-file
 *   POST   /evals/run  ·  GET /evals/status
 *   POST   /feedback
 */
import { GRAPH_ID, apiHeaders, getApiBase } from "./config";
import { splitStreamEvent } from "./streamEvent";

/* ----------------------------- Domain types ----------------------------- */

/** A quick-action preset shown in the chat pane (assistant metadata). */
export interface QuickAction {
  label: string;
  question: string;
}

/** Per-assistant branding + provenance, stored in the assistant's metadata. */
export interface AssistantMetadata {
  display_name?: string;
  accent?: string;
  accent2?: string;
  logo?: string;
  actions?: QuickAction[];
  owner_name?: string;
  customer?: string;
  /** Industry label (e.g. "Retail") — used in the chat hero placeholder + dropdown. */
  industry?: string;
  /** Brand-appropriate default theme applied when this assistant is selected. */
  theme?: "light" | "dark";
  /** Presenter brief bullets shown in a popup once setup finishes. */
  demo_brief?: string[];
  /** Recommended demo-flow steps shown alongside the brief. */
  demo_flow?: string[];
  /** Handles of the LangSmith artifacts this assistant created, for cascade cleanup on delete. */
  ls_artifacts?: LsArtifacts;
  [key: string]: unknown;
}

/** LangSmith artifacts a setup run created; deleted together when the assistant is removed. */
export interface LsArtifacts {
  workspace?: string;
  project?: string;
  prompt_name?: string;
  agent_repo?: string;
  skills_repo?: string;
  skills?: string[];
  /**
   * LangSmith dataset the setup run provisioned for this assistant's demo
   * evals. Absent on every assistant created before the eval feature (and
   * whenever the best-effort dataset creation failed) — treat that as "this
   * assistant has no evals", never as an error. /cleanup deletes it alongside
   * the other artifacts, so it only has to be present in this object.
   */
  eval_dataset?: string;
  /**
   * Run rule attaching the LLM-as-judge evaluator to `eval_dataset`. Absent when
   * attaching failed or for assistants created before it existed — in which case the
   * experiment falls back to grading in-process. /cleanup deletes it explicitly.
   */
  eval_rule_id?: string;
  /**
   * The workspace evaluator that rule points at — the row on LangSmith's Evaluators
   * page. A separate object from the rule, so /cleanup deletes both.
   */
  eval_evaluator_id?: string;
  /** Prompt Hub prompt holding the judge that `eval_evaluator_id` references. */
  eval_judge_prompt?: string;
  /**
   * Name (not id) of the human-review queue over the trace project — the backfill
   * creates it minutes after setup, so the deterministic name is the handle recorded
   * here. /cleanup resolves it to an id and deletes it.
   */
  annotation_queue?: string;
}

/** A server-side assistant: a stored configuration instance of the graph. */
export interface Assistant {
  assistant_id: string;
  graph_id: string;
  name?: string;
  context?: RunContext & Record<string, unknown>;
  config?: Record<string, unknown>;
  metadata?: AssistantMetadata;
  created_at?: string;
  updated_at?: string;
}

/** A LangSmith workspace (tenant), from GET /workspaces. */
export interface Workspace {
  id: string;
  name?: string;
}

/**
 * Per-run runtime context (dashboard_agent.agent.Context). Sent as
 * `{ context: {...} }` in the run body — NOT config.configurable. Only
 * non-empty fields should be included; the backend prefers `prompt` over
 * `prompt_name`. `ls_workspace`/`ls_project` ride here for trace routing.
 */
export interface RunContext {
  prompt?: string;
  prompt_name?: string;
  /** Context Hub agent repo whose AGENTS.md is the system prompt. */
  agent_repo?: string;
  data_prompt?: string;
  data_gap?: string;
  ls_workspace?: string;
  ls_project?: string;
  /**
   * Catalogue tool ids this assistant exposes. Omit when the assistant has no
   * saved selection (the backend then applies its defaults). An EMPTY ARRAY is
   * meaningful — "every optional tool off" — and must be sent, not omitted.
   */
  enabled_tools?: string[];
}

/** One selectable capability, from GET /tools (the backend registry). */
export interface ToolSpec {
  id: string;
  label: string;
  description: string;
  group: string;
  always_on: boolean;
  default_on: boolean;
}

/* ---- Widget specs (the agent's push_widget payloads) ---- */

export type WidgetType = "kpi" | "bar" | "line" | "pie" | "table" | "text";

export interface WidgetPoint {
  label: string;
  value: number;
}

export interface WidgetSeries {
  name?: string;
  points: WidgetPoint[];
}

export interface KpiWidget {
  type: "kpi";
  title: string;
  value: string | number;
  unit?: string;
  delta?: string;
  trend?: "up" | "down" | "flat";
  description?: string;
}

export interface ChartWidget {
  type: "bar" | "line" | "pie";
  title: string;
  x_label?: string;
  y_label?: string;
  series: WidgetSeries[];
}

export interface TableWidget {
  type: "table";
  title: string;
  columns: string[];
  rows: Array<Array<string | number>>;
}

export interface TextWidget {
  type: "text";
  title: string;
  content: string;
}

export type Widget = KpiWidget | ChartWidget | TableWidget | TextWidget;

/* ---- Message + stream shapes ---- */

export type MessageContent =
  | string
  | Array<{ type?: string; text?: string } | string>;

export interface ToolCall {
  id?: string;
  name?: string;
  args?: Record<string, unknown>;
}

/** A message as it appears in stream events and thread state. */
export interface ThreadMessage {
  id?: string;
  type?: string; // "human" | "ai" | "tool" | ...
  role?: string;
  content?: MessageContent;
  tool_calls?: ToolCall[];
  tool_call_id?: string;
  name?: string;
}

export interface ThreadState {
  values?: { messages?: ThreadMessage[] };
  [key: string]: unknown;
}

/** One decoded SSE block from runStream. `data` is the raw (JSON) string. */
export interface SSEEvent {
  event: string;
  data: string;
  /**
   * Subgraph namespace path parsed off the SSE event-name `|` suffix (present
   * when the run is streamed with `stream_subgraphs`). `[]` = the main graph;
   * `["tools:<task_call_id>"]` = a task-dispatched subagent; deeper = nested.
   */
  namespace: string[];
}

/* ---- assistant_setup graph (runWait) ---- */

/** Input to the deployed assistant_setup graph. */
export interface SetupInput {
  workspace: string;
  customer: string;
  owner?: string;
  website?: string;
  /** Optional NL scenario — tailors personas, data-gap, tools, and the prompt. */
  use_case?: string;
  /** Named failure mode to build in ("none" | "hallucination"). */
  failure_mode?: string;
  /** Legacy boolean; maps to failure_mode="hallucination" on the backend. */
  hallucination?: boolean;
  push_prompts?: boolean;
  /** Where the prompt is stored: "context_hub" (AGENTS.md, the default) or "prompt_hub". */
  prompt_source?: "prompt_hub" | "context_hub";
  /**
   * Backfill the new assistant's trace project with a day of synthetic traffic.
   * OPT-IN: it is thousands of runs the customer never made, carrying a LangSmith
   * cost estimate in the hundreds. Settings can generate it later instead.
   */
  demo_traffic?: boolean;
  /** Capabilities the new assistant starts with; editable afterwards. */
  enabled_tools?: string[];
  /**
   * Voice mode: adds the mic button on this assistant. OPT-IN, and never inferred from
   * the use case. Lands in the assistant's metadata rather than its runtime context,
   * because the agent knows nothing about voice (see lib/voice.ts).
   */
  voice?: { voice_name?: string };
}

/** Prepared payload the assistant_setup graph returns. */
export interface SetupResult {
  context?: RunContext & Record<string, unknown>;
  metadata?: AssistantMetadata;
  prompt_urls?: string[];
  [key: string]: unknown;
}

/** Raw result of a runs/wait invocation. */
export interface RunWaitResult {
  status?: string;
  error?: string;
  result?: SetupResult;
  [key: string]: unknown;
}

/** Input to createAssistant. */
export interface CreateAssistantInput {
  name: string;
  context?: Record<string, unknown>;
  config?: Record<string, unknown>;
  metadata?: AssistantMetadata;
}

/** Body accepted by updateAssistant (PATCH). */
export interface UpdateAssistantInput {
  name?: string;
  context?: Record<string, unknown>;
  config?: Record<string, unknown>;
  metadata?: AssistantMetadata;
}

/** Input to postFeedback. */
export interface FeedbackInput {
  run_id: string;
  score: number;
  comment?: string;
  feedback_id?: string;
  /** Workspace the run traced to, so feedback targets the same tenant. */
  workspace?: string;
}

/** Result of postFeedback (served by webapp.py). */
export interface FeedbackResult {
  ok?: boolean;
  feedback_id?: string;
  error?: string;
}

/* ------------------------------- Helpers -------------------------------- */

/** Extract a useful error message from a non-OK response body. */
async function errorFrom(res: Response): Promise<Error> {
  const d = await res.json().catch(() => ({}) as Record<string, unknown>);
  const msg = (d.error as string) || (d.detail as string) || `HTTP ${res.status}`;
  return new Error(msg);
}

/* ------------------------------- Threads -------------------------------- */

/** Create a fresh server-side thread; returns its id. */
export async function createThread(): Promise<string> {
  const res = await fetch(`${getApiBase()}/threads`, {
    method: "POST",
    headers: apiHeaders(),
    body: "{}",
  });
  if (!res.ok) throw new Error("create thread failed: HTTP " + res.status);
  return (await res.json()).thread_id as string;
}

let THREAD_ID: string | null = null;

/**
 * Return a memoized thread id, minting one on first use so follow-up questions
 * share memory. Mirrors the original module-level `ensureThread()`.
 */
export async function ensureThread(): Promise<string> {
  if (THREAD_ID) return THREAD_ID;
  THREAD_ID = await createThread();
  return THREAD_ID;
}

/** Adopt an existing thread id (e.g. when the user selects a past thread). */
export function setThreadId(id: string | null): void {
  THREAD_ID = id;
}

/** Current memoized thread id, if any. */
export function getThreadId(): string | null {
  return THREAD_ID;
}

/** Drop the memoized thread so the next ensureThread() mints a new one. */
export function resetThread(): void {
  THREAD_ID = null;
}

/** Fetch a thread's persisted state (its message history). */
export async function getThreadState(id: string): Promise<ThreadState> {
  const res = await fetch(`${getApiBase()}/threads/${id}/state`, {
    headers: apiHeaders(),
  });
  if (!res.ok) throw await errorFrom(res);
  return res.json();
}

/* --------------------------------- Runs --------------------------------- */

/** A human-in-the-loop pause raised by a tool (see tools/simulated.py `review`). */
export interface ReviewInterrupt {
  /** Which editor to show — "email_draft" | "meeting_slots". */
  kind: string;
  /** The generated artifact awaiting review. */
  draft: Record<string, unknown>;
  [key: string]: unknown;
}

export interface RunStreamOptions {
  threadId: string;
  assistantId: string;
  /**
   * The user turn(s) to send as run input. Omit when resuming — a resume must
   * NOT re-send input or the turn is duplicated.
   *
   * `content` is a plain string for a text-only turn, or LangChain content blocks
   * when the turn carries an image (see `imageContent`).
   */
  messages?: Array<{ role: string; content: string | Array<Record<string, unknown>> }>;
  /** Per-run runtime context; omitted from the body when empty. */
  context?: RunContext;
  /** Resume a run paused at an interrupt, with the human's reviewed value. */
  resume?: unknown;
  /** Optional abort signal to cancel the stream. */
  signal?: AbortSignal;
  /**
   * Extra request headers. Used by voice mode for LangSmith distributed tracing:
   * `langsmith-trace` + `baggage`, minted by POST /voice/trace, make this run nest
   * under the conversation's tool span instead of starting its own trace (graph.py
   * turns them back into a tracing parent).
   */
  headers?: Record<string, string>;
  /**
   * The active `/goal`, sent as deepagents' `rubric` state key. `RubricMiddleware`
   * grades each finished turn against it and sends the agent back for another pass
   * until it is satisfied; it no-ops when this is absent. Re-sent every turn — the
   * goal is sticky until the user clears it or the grader passes it.
   */
  rubric?: string;
}

/**
 * Stream a run over SSE (stream_mode:"messages") and yield each decoded
 * `{ event, data }` block. Framing is CRLF-normalized (\r stripped) so
 * `\r\n\r\n` boundaries parse as `\n\n`, matching the original reader. `data`
 * is the raw string (typically JSON) — the caller parses it.
 */
export async function* runStream(opts: RunStreamOptions): AsyncGenerator<SSEEvent> {
  const { threadId, assistantId, messages, context, resume, signal, rubric, headers } = opts;
  const body: Record<string, unknown> = {
    assistant_id: assistantId,
    // "updates" carries `__interrupt__` when a tool pauses for human review;
    // "messages" is the token stream the chat + widgets are built from; "custom"
    // carries RubricMiddleware's `rubric_evaluation_*` frames (the goal verdict).
    stream_mode: ["messages", "updates", "custom"],
    // Also stream frames emitted from inside subgraphs (task-dispatched
    // subagents) so we can peer into their work. Their event names carry a `|`
    // namespace suffix; the root graph's frames stay unsuffixed.
    stream_subgraphs: true,
  };
  if (resume !== undefined) {
    // A resume replaces input entirely — sending both would duplicate the turn.
    body.command = { resume };
  } else {
    // A rubric rides on the INPUT, not the context: it is agent state, and the
    // middleware compares it to the previous turn's to decide whether this is the
    // same grading run or a new one. ALWAYS sent, empty when there is no goal —
    // state is checkpointed, so merely omitting the key would leave a cleared
    // goal grading every future turn on the thread. Empty reads as "no rubric".
    body.input = { messages: messages || [], rubric: rubric || "" };
  }
  if (context && Object.keys(context).length) body.context = context;

  const res = await fetch(`${getApiBase()}/threads/${threadId}/runs/stream`, {
    method: "POST",
    headers: { ...apiHeaders(), ...(headers || {}) },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) throw await errorFrom(res);

  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    // Strip CR so CRLF / `\r\n\r\n` SSE framing normalizes to `\n` / `\n\n`.
    buf += dec.decode(value, { stream: true }).replace(/\r/g, "");
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const raw = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let event = "message";
      const dataLines: string[] = [];
      for (const line of raw.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
      }
      if (dataLines.length) {
        // The server suffixes the event name with the emitting subgraph's
        // namespace (`event|tools:abc|…`) when stream_subgraphs is on; split it
        // off so the caller can route root vs subagent frames.
        const { event: base, namespace } = splitStreamEvent(event);
        yield { event: base, data: dataLines.join("\n"), namespace };
      }
    }
  }
}

/**
 * Run an assistant to completion (runs/wait) and return the raw result.
 * Used for non-streaming graphs such as assistant_setup.
 */
export async function runWait(
  threadId: string,
  assistantId: string,
  input: unknown,
): Promise<RunWaitResult> {
  const res = await fetch(`${getApiBase()}/threads/${threadId}/runs/wait`, {
    method: "POST",
    headers: apiHeaders(),
    body: JSON.stringify({ assistant_id: assistantId, input }),
  });
  if (!res.ok) throw await errorFrom(res);
  return res.json();
}

/**
 * Run the deployed assistant_setup graph and return its prepared payload
 * (metadata + context + prompt_urls). Creates a throwaway thread, waits for the
 * result, and surfaces graph-level errors. Mirrors the original `lgRunSetup()`.
 */
export async function runSetup(input: SetupInput): Promise<SetupResult> {
  const tid = await createThread();
  const out = await runWait(tid, "assistant_setup", input);
  if (out && out.status === "error") throw new Error(out.error || "setup failed");
  return (out && out.result) || {};
}

/* ------------------------------ Assistants ------------------------------ */

/**
 * List assistants for the graph (POST /assistants/search). Empty on failure.
 * Excludes the graph-default assistant LangGraph auto-creates per graph on
 * startup (name === graph_id, or `metadata.created_by === "system"`): it can't
 * be permanently deleted (the server recreates it) and isn't a real customer.
 */
export async function listAssistants(limit = 100): Promise<Assistant[]> {
  try {
    const res = await fetch(`${getApiBase()}/assistants/search`, {
      method: "POST",
      headers: apiHeaders(),
      body: JSON.stringify({ graph_id: GRAPH_ID, limit }),
    });
    if (!res.ok) return [];
    const list = await res.json();
    if (!Array.isArray(list)) return [];
    return (list as Assistant[]).filter(
      (a) => a.name !== GRAPH_ID && a.metadata?.created_by !== "system",
    );
  } catch {
    return [];
  }
}

/** Fetch a single assistant by id (GET /assistants/{id}). */
export async function getAssistant(id: string): Promise<Assistant> {
  const res = await fetch(`${getApiBase()}/assistants/${id}`, {
    headers: apiHeaders(),
  });
  if (!res.ok) throw await errorFrom(res);
  return res.json();
}

/** Create a new assistant (POST /assistants). */
export async function createAssistant(input: CreateAssistantInput): Promise<Assistant> {
  const res = await fetch(`${getApiBase()}/assistants`, {
    method: "POST",
    headers: apiHeaders(),
    body: JSON.stringify({
      graph_id: GRAPH_ID,
      name: input.name,
      context: input.context || {},
      config: input.config || {},
      metadata: input.metadata || {},
    }),
  });
  if (!res.ok) throw await errorFrom(res);
  return res.json();
}

/** Update an existing assistant (PATCH /assistants/{id}). */
export async function updateAssistant(id: string, body: UpdateAssistantInput): Promise<Assistant> {
  const res = await fetch(`${getApiBase()}/assistants/${id}`, {
    method: "PATCH",
    headers: apiHeaders(),
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await errorFrom(res);
  return res.json();
}

/** Delete an assistant (DELETE /assistants/{id}). 204 is treated as success. */
export async function deleteAssistant(id: string): Promise<void> {
  const res = await fetch(`${getApiBase()}/assistants/${id}`, {
    method: "DELETE",
    headers: apiHeaders(),
  });
  if (!res.ok && res.status !== 204) throw await errorFrom(res);
}

/**
 * Best-effort cascade delete of the LangSmith artifacts a setup run created
 * (trace project, Prompt/Context Hub repo, linked skills). Never throws — a
 * cleanup failure must not block deleting the assistant record itself. Returns
 * the server's per-artifact report so callers can surface partial failures.
 */
export async function cleanupAssistantArtifacts(
  refs: LsArtifacts,
): Promise<{ deleted: string[]; failed: { artifact: string; error: string }[] }> {
  try {
    const res = await fetch(`${getApiBase()}/cleanup`, {
      method: "POST",
      headers: apiHeaders(),
      body: JSON.stringify(refs),
    });
    return await res.json();
  } catch {
    return { deleted: [], failed: [] };
  }
}

/**
 * Resolve the LangSmith trace URL for a run (the debug link under an answer).
 * Server-side lookup so the LangSmith key never reaches the client.
 */
export async function getTraceUrl(runId: string, workspace?: string): Promise<string> {
  const qs = new URLSearchParams({ run_id: runId, ...(workspace ? { workspace } : {}) });
  const res = await fetch(`${getApiBase()}/trace-url?${qs}`, { headers: apiHeaders() });
  const d = (await res.json().catch(() => ({}))) as { url?: string; error?: string };
  if (!res.ok || !d.url) throw new Error(d.error || `HTTP ${res.status}`);
  return d.url;
}

/* ---------------------- Workspaces / projects / prompts ------------------ */

/** List LangSmith workspaces (GET /workspaces). Empty on failure. */
export async function listWorkspaces(): Promise<Workspace[]> {
  try {
    const res = await fetch(`${getApiBase()}/workspaces`, { headers: apiHeaders() });
    if (!res.ok) return [];
    const d = await res.json();
    return Array.isArray(d.workspaces) ? d.workspaces : [];
  } catch {
    return [];
  }
}

/** List tracing project names for a workspace (GET /projects). Empty on failure. */
export async function listProjects(workspace?: string): Promise<string[]> {
  try {
    const qs = workspace ? `?workspace=${encodeURIComponent(workspace)}` : "";
    const res = await fetch(`${getApiBase()}/projects${qs}`, { headers: apiHeaders() });
    if (!res.ok) return [];
    const d = await res.json();
    return Array.isArray(d.projects) ? d.projects : [];
  } catch {
    return [];
  }
}

/** Create a tracing project (POST /projects). */
export async function createProject(name: string, workspace?: string): Promise<unknown> {
  const res = await fetch(`${getApiBase()}/projects`, {
    method: "POST",
    headers: apiHeaders(),
    body: JSON.stringify({ name, workspace: workspace || undefined }),
  });
  if (!res.ok) throw await errorFrom(res);
  return res.json();
}

/** List the selectable tool catalogue (GET /tools). Empty on failure. */
export async function listTools(): Promise<ToolSpec[]> {
  try {
    const res = await fetch(`${getApiBase()}/tools`, { headers: apiHeaders() });
    if (!res.ok) return [];
    const d = await res.json();
    return Array.isArray(d.tools) ? d.tools : [];
  } catch {
    return [];
  }
}

/** List Prompt Hub prompt names for a workspace (GET /hub-prompts). Empty on failure. */
export async function listHubPrompts(workspace?: string): Promise<string[]> {
  try {
    const qs = workspace ? `?workspace=${encodeURIComponent(workspace)}` : "";
    const res = await fetch(`${getApiBase()}/hub-prompts${qs}`, { headers: apiHeaders() });
    if (!res.ok) return [];
    const d = await res.json();
    return Array.isArray(d.prompts) ? d.prompts : [];
  } catch {
    return [];
  }
}

/** List Context Hub agent repos for a workspace (GET /agents). Empty on failure. */
export async function listAgents(workspace?: string): Promise<string[]> {
  try {
    const qs = workspace ? `?workspace=${encodeURIComponent(workspace)}` : "";
    const res = await fetch(`${getApiBase()}/agents${qs}`, { headers: apiHeaders() });
    if (!res.ok) return [];
    const d = await res.json();
    return Array.isArray(d.agents) ? d.agents : [];
  } catch {
    return [];
  }
}

/* ----------------------------- Sandbox files ----------------------------- */

/**
 * How the server classified a directory entry. Derived from the extension
 * allowlist, not from the bytes — so the UI can pick an icon and grey out
 * non-previewable files before the user clicks one.
 */
/** "media" is a binary the BROWSER can render (PDF, image) - shipped as base64. */
export type SandboxKind = "dir" | "text" | "binary" | "media";

/**
 * Which sandbox VM to browse. Both fields come from the ACTIVE ASSISTANT'S
 * metadata (`ls_artifacts.agent_repo` and `customer`) and mirror the key the
 * agent itself uses. Blank strings must be omitted, not sent: assistant_setup
 * writes `ls_artifacts.agent_repo = ""` when there is no Context Hub repo.
 */
export interface SandboxTarget {
  agent_repo?: string;
  customer?: string;
}

/** One entry in a sandbox directory listing. Dirs first, then case-insensitive name. */
export interface SandboxEntry {
  name: string;
  /** Absolute path on the sandbox VM — also the tree node id. */
  path: string;
  is_dir: boolean;
  kind: SandboxKind;
}

/** One directory listing (GET /sandbox-files). */
export interface SandboxListing {
  /** Sandbox root (e.g. "/workspace") — the tree's root item id. */
  root: string;
  /** The directory that was listed. */
  path: string;
  /** Parent directory, or null when `path` is the root. */
  parent: string | null;
  entries: SandboxEntry[];
  /** True when the directory held more entries than the server's cap. */
  truncated: boolean;
  /** The VM name (e.g. "da-acme-agents"), handy in a footer. */
  sandbox_id?: string | null;
  /**
   * Client-populated: why the listing failed (no sandbox, no entitlement,
   * network, HTTP error). Set instead of throwing so the tree still renders.
   */
  error?: string;
}

/** One file read from the sandbox (GET /sandbox-file). */
export interface SandboxFile {
  path: string;
  name: string;
  kind: SandboxKind;
  /** "markdown" for .md/.mdx/.markdown, else the bare extension; null if none. */
  language: string | null;
  encoding: string | null;
  /** null when the file is not previewable — see `reason` / `message`. */
  content: string | null;
  /** Content type, set only for `kind: "media"` (its `content` is base64). */
  mime?: string;
  offset: number;
  limit: number;
  /**
   * True when this is NOT the whole file: the page filled `limit` lines, or the
   * in-VM ~500 KiB cap or the server's 256 KiB cap fired.
   */
  truncated: boolean;
  /**
   * Line to resume from for the next page, or null on the last page (also null
   * when a single line is itself over a byte cap, so paging can't advance).
   */
  next_offset: number | null;
  /** Present only when `content` is null. */
  reason?: "binary" | "not_previewable" | "too_large";
  /** Human-readable companion to `reason`, safe to show verbatim. */
  message?: string;
  sandbox_id?: string | null;
}

/** Shared `?agent_repo=&customer=` query for the sandbox routes; blanks are omitted. */
function sandboxQuery(target: SandboxTarget, extra: Record<string, string>): URLSearchParams {
  const qs = new URLSearchParams(extra);
  if (target.agent_repo) qs.set("agent_repo", target.agent_repo);
  if (target.customer) qs.set("customer", target.customer);
  return qs;
}

/**
 * List ONE directory on the assistant's sandbox (GET /sandbox-files); omit
 * `path` for the root. Lazy by design — the tree calls this per expanded node.
 *
 * Never throws: the sandbox is legitimately absent (DA_SANDBOX=0, no key, no
 * entitlement), so failures come back as an empty listing carrying `error`.
 */
export async function listSandboxFiles(
  target: SandboxTarget,
  path?: string,
): Promise<SandboxListing> {
  const failed = (error: string): SandboxListing => ({
    root: path ?? "",
    path: path ?? "",
    parent: null,
    entries: [],
    truncated: false,
    error,
  });
  try {
    const qs = sandboxQuery(target, path ? { path } : {});
    const res = await fetch(`${getApiBase()}/sandbox-files?${qs}`, { headers: apiHeaders() });
    if (!res.ok) return failed((await errorFrom(res)).message);
    const d = (await res.json()) as Partial<SandboxListing>;
    return {
      root: d.root ?? path ?? "",
      path: d.path ?? path ?? "",
      parent: d.parent ?? null,
      entries: Array.isArray(d.entries) ? d.entries : [],
      truncated: !!d.truncated,
      sandbox_id: d.sandbox_id ?? null,
    };
  } catch (e) {
    return failed(e instanceof Error ? e.message : String(e));
  }
}

/**
 * Read one file from the assistant's sandbox (GET /sandbox-file). Throws so the
 * viewer pane can show the real message; a binary/oversized file is a SUCCESS
 * with `content: null` plus `reason`/`message`, not an error.
 */
export async function readSandboxFile(
  target: SandboxTarget,
  path: string,
  opts: { offset?: number; limit?: number } = {},
): Promise<SandboxFile> {
  const extra: Record<string, string> = { path };
  if (opts.offset) extra.offset = String(opts.offset);
  if (opts.limit) extra.limit = String(opts.limit);
  const res = await fetch(`${getApiBase()}/sandbox-file?${sandboxQuery(target, extra)}`, {
    headers: apiHeaders(),
  });
  if (!res.ok) throw await errorFrom(res);
  return res.json();
}

/** Outcome of an upload (POST /sandbox-upload). Never throws; check `failed`. */
export interface SandboxUploadResult {
  dir: string;
  written: { name: string; path: string }[];
  failed: { name: string; error: string }[];
}

/** Files bigger than this are rejected server-side, so stop them here too. */
export const SANDBOX_UPLOAD_MAX_BYTES = 15 * 1024 * 1024;
export const SANDBOX_UPLOAD_MAX_FILES = 5;

/**
 * Upload files into the assistant's VM, where the agent can read them.
 *
 * This is the only channel for getting a document to the agent — it has no way to
 * receive an attachment in chat — so the prompt tells it to ask for uploads here.
 *
 * Base64 in JSON rather than multipart, matching the server (no python-multipart in
 * the deployment). Attach-only on the server side: an assistant whose VM has been
 * reaped answers 503 and the caller is told to send a message first.
 */
export async function uploadSandboxFiles(
  target: SandboxTarget,
  files: File[],
): Promise<SandboxUploadResult> {
  const payload = await Promise.all(
    files.map(async (file) => ({
      name: file.name,
      // FileReader would need a callback dance; arrayBuffer + chunked btoa keeps this
      // synchronous-ish and avoids blowing the call stack on a multi-MB spread.
      content_b64: bytesToBase64(new Uint8Array(await file.arrayBuffer())),
    })),
  );
  const res = await fetch(`${getApiBase()}/sandbox-upload`, {
    method: "POST",
    headers: apiHeaders(),
    body: JSON.stringify({
      agent_repo: target.agent_repo || undefined,
      customer: target.customer || undefined,
      files: payload,
    }),
  });
  const body = (await res.json().catch(() => ({}))) as Partial<SandboxUploadResult> & {
    error?: string;
  };
  if (!res.ok && !body.failed?.length) throw await errorFrom(res);
  return {
    dir: body.dir ?? "",
    written: body.written ?? [],
    failed: body.failed ?? [],
  };
}

/* ------------------------------ Image input ------------------------------ */

/** An image the user attached to a chat turn, ready to send as a content block. */
export interface ImageAttachment {
  name: string;
  /** e.g. "image/png" — Anthropic accepts png, jpeg, gif and webp. */
  mime: string;
  /** Base64 payload with no data: prefix. */
  data: string;
}

/** What Anthropic will accept, so a .tiff is refused before it reaches a run. */
export const IMAGE_MIME_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"];
/** Images are re-encoded as base64 into the thread, so keep them small. */
export const IMAGE_MAX_BYTES = 5 * 1024 * 1024;

/** Read a browser File into an attachment, or reject it with a reason. */
export async function readImageAttachment(
  file: File,
): Promise<{ image?: ImageAttachment; error?: string }> {
  if (!IMAGE_MIME_TYPES.includes(file.type)) {
    return { error: `${file.name || "that file"} is not a PNG, JPEG, GIF or WebP.` };
  }
  if (file.size > IMAGE_MAX_BYTES) {
    return { error: `${file.name} is over ${Math.round(IMAGE_MAX_BYTES / (1024 * 1024))}MB.` };
  }
  const data = bytesToBase64(new Uint8Array(await file.arrayBuffer()));
  return { image: { name: file.name || "pasted image", mime: file.type, data } };
}

/**
 * The `content` for a user turn: a bare string, or blocks when images are attached.
 *
 * Block shape is LangChain's standard image block (`{type, base64, mime_type}`), which
 * langchain_anthropic rewrites into Anthropic's `source`/`media_type` form — verified
 * against the installed version rather than assumed, since the older
 * `{source_type, data}` spelling and OpenAI's `image_url` both also parse and it is not
 * obvious from the outside which one survives.
 */
export function imageContent(
  text: string,
  images: ImageAttachment[],
): string | Array<Record<string, unknown>> {
  if (!images.length) return text;
  return [
    ...(text ? [{ type: "text", text }] : []),
    ...images.map((img) => ({ type: "image", base64: img.data, mime_type: img.mime })),
  ];
}

/** btoa in 32KB chunks: String.fromCharCode(...bytes) overflows the stack on big files. */
function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

/* --------------------------------- Evals --------------------------------- */

/**
 * Which assistant's demo eval dataset to act on. The server keeps no assistant
 * store of its own and does NOT look the assistant up, so everything it needs
 * rides in the request — the same way the sandbox routes carry
 * `agent_repo`/`customer` rather than resolving them server-side. All of it
 * comes off the assistant object the SPA already holds. Blanks are omitted.
 */
export interface EvalTarget {
  assistant_id: string;
  /** Dataset name from `metadata.ls_artifacts.eval_dataset`. */
  dataset?: string;
  /** Workspace the dataset + experiments live in (`context.ls_workspace`). */
  workspace?: string;
  /**
   * Trace project (`context.ls_project`). Only used to show demo-traffic state
   * and the LangSmith deep links alongside the eval — the experiment itself does
   * not need it.
   */
  project?: string;
  /**
   * The assistant's stored `context`, forwarded verbatim to the experiment
   * target (POST /evals/run only). REQUIRED for a meaningful score: the server
   * rebuilds the runtime Context from this, so omitting it grades a default
   * agent — wrong prompt handle, wrong customer, wrong planted gap — and the
   * demo's 2/3 baseline would be an accident rather than the planted bug.
   */
  context?: RunContext & Record<string, unknown>;
}

/**
 * Latest state of an assistant's eval dataset (GET /evals/status). LangSmith is
 * the source of truth — there is no server-side run store — so this survives a
 * page reload mid-experiment.
 *
 * `dataset_name: null` is the ordinary "this assistant has no evals" answer, NOT
 * a failure; `error` is only set when the lookup itself broke.
 */
export interface EvalStatus {
  /** null when the assistant has no eval dataset. */
  dataset_name: string | null;
  /** Deep link to the dataset in LangSmith. */
  dataset_url?: string | null;
  /** An experiment is in flight right now. */
  running: boolean;
  /** Name of the most recent experiment, if one has ever run. */
  experiment_name?: string | null;
  /** Deep link to that experiment in LangSmith. */
  url?: string | null;
  /** Examples that scored 1 (correct behaviour) in the latest experiment. */
  passed?: number | null;
  /** Examples in the latest experiment. */
  total?: number | null;
  /** Set only when the status lookup failed. */
  error?: string;
  /**
   * Why the last experiment we started died, when it did. Distinct from `error`:
   * the lookup worked fine, the RUN did not — and it usually died before it
   * created anything in LangSmith (a missing model key, a workspace the server's
   * key cannot see), so without this the panel would just show the previous score
   * forever and the presenter would read a dead run as "the fix did nothing".
   */
  last_error?: string | null;
}

/** Acknowledgement of POST /evals/run — the experiment itself runs detached. */
export interface EvalRunAck {
  ok: boolean;
  dataset_name?: string | null;
  error?: string;
}

/** Shared `?assistant_id=&dataset=&workspace=` query; blanks are omitted. */
function evalQuery(target: EvalTarget): URLSearchParams {
  const qs = new URLSearchParams({ assistant_id: target.assistant_id });
  if (target.dataset) qs.set("dataset", target.dataset);
  if (target.workspace) qs.set("workspace", target.workspace);
  return qs;
}

/**
 * Read the latest experiment for an assistant's eval dataset
 * (GET /evals/status). Also the poll used while a run is in flight.
 *
 * Never throws. An assistant without a dataset is the common case (every
 * assistant predating this feature), and the panel renders that as a calm empty
 * state — so a 404 comes back as `dataset_name: null` with no `error` set, and
 * only a genuine transport/server failure fills `error`.
 */
export async function getEvalStatus(target: EvalTarget): Promise<EvalStatus> {
  const empty: EvalStatus = { dataset_name: null, running: false };
  try {
    const res = await fetch(`${getApiBase()}/evals/status?${evalQuery(target)}`, {
      headers: apiHeaders(),
    });
    if (res.status === 404) return empty;
    if (!res.ok) return { ...empty, error: (await errorFrom(res)).message };
    const d = (await res.json()) as Partial<EvalStatus>;
    return {
      dataset_name: d.dataset_name || null,
      dataset_url: d.dataset_url ?? null,
      running: !!d.running,
      experiment_name: d.experiment_name ?? null,
      url: d.url ?? null,
      passed: typeof d.passed === "number" ? d.passed : null,
      total: typeof d.total === "number" ? d.total : null,
      error: d.error,
      last_error: d.last_error ?? null,
    };
  } catch (e) {
    return { ...empty, error: e instanceof Error ? e.message : String(e) };
  }
}

/**
 * Kick off an experiment over the assistant's eval dataset (POST /evals/run).
 * Returns as soon as the server has spawned the run — three real agent turns
 * take 30-90s, so progress is observed through getEvalStatus, not this call.
 *
 * Never throws, deliberately: this is also fired forget-style right after an
 * assistant is created (the baseline run), where a rejection would surface as
 * an unhandled promise or, worse, break the create path.
 */
export async function runEvalExperiment(target: EvalTarget): Promise<EvalRunAck> {
  try {
    const res = await fetch(`${getApiBase()}/evals/run`, {
      method: "POST",
      headers: apiHeaders(),
      body: JSON.stringify({
        assistant_id: target.assistant_id,
        dataset: target.dataset || undefined,
        workspace: target.workspace || undefined,
        // Not decoration: the target is built from this dict, so a run without
        // it evaluates a default agent instead of this assistant's.
        context: target.context || undefined,
      }),
    });
    if (!res.ok) return { ok: false, error: (await errorFrom(res)).message };
    const d = (await res.json()) as Partial<EvalRunAck>;
    return { ok: d.ok !== false, dataset_name: d.dataset_name ?? null, error: d.error };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

/* ------------------------------ Demo traffic ----------------------------- */

/** What POST /demo-traffic needs to backfill an assistant's trace project. */
export interface DemoTrafficTarget {
  /** Trace project to fill — `context.ls_project`, i.e. the customer name. */
  project: string;
  workspace?: string;
  /** The assistant's stored context; the seed runs are made with it. */
  context?: RunContext & Record<string, unknown>;
  /** Finalized quick actions — the seed questions come from these. */
  actions?: { question?: string; kind?: string }[];
  data_gap?: string;
  customer?: string;
}

/** Progress of the last backfill (GET /demo-traffic/status). In-process only. */
export interface DemoTrafficStatus {
  project: string;
  running: boolean;
  /**
   * What LangSmith itself reports about the backfill, counted from the
   * `synthetic-demo` tag on every seeded run. Durable: unlike `result` below this
   * survives a redeploy, a reload and a second browser, so prefer it whenever
   * `traces` is set. `{}` when the project does not exist yet.
   */
  traffic?: { traces?: number; newest?: string };
  /**
   * The in-process receipt from the run that produced the traffic — richer (gap
   * traces, the Insights job outcome) but ephemeral, and absent for a backfill any
   * other process did.
   */
  result?: {
    traces?: number;
    runs?: number;
    gap_traces?: number;
    hours?: number;
    error?: string;
    insights?: { job_error?: string };
    engine?: { enabled?: boolean; already_enabled?: boolean; error?: string };
  };
  /**
   * LangSmith deep links for the trace project and its tabs. Empty when the
   * project does not exist yet (no traffic has been generated) — that is the
   * normal pre-backfill state, not an error.
   */
  links?: { project?: string; insights?: string; engine?: string };
}

/**
 * Start a synthetic-traffic backfill for an assistant's trace project.
 *
 * Returns as soon as the server spawns the job — it makes several real agent
 * runs for seeds and then ingests a few thousand backdated runs, so progress is
 * observed through getDemoTrafficStatus, not this call.
 *
 * Never throws: this is also fired forget-style from the create path.
 */
export async function generateDemoTraffic(
  target: DemoTrafficTarget,
): Promise<{ ok: boolean; error?: string }> {
  try {
    const res = await fetch(`${getApiBase()}/demo-traffic`, {
      method: "POST",
      headers: apiHeaders(),
      body: JSON.stringify({
        project: target.project,
        workspace: target.workspace || undefined,
        context: target.context || undefined,
        actions: target.actions || undefined,
        data_gap: target.data_gap || undefined,
        customer: target.customer || undefined,
      }),
    });
    if (!res.ok) return { ok: false, error: (await errorFrom(res)).message };
    const d = (await res.json()) as { ok?: boolean; error?: string };
    return { ok: d.ok !== false, error: d.error };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

/** Poll a backfill's progress. Never throws; a dead server reads as "not running". */
export async function getDemoTrafficStatus(
  project: string,
  /**
   * Required in practice for the `links` to come back. The server resolves the
   * project URL with a workspace-scoped client, and without this it falls back
   * to the key's default tenant, fails the lookup, and returns no links at all
   * (silently, since absent links are also the legitimate pre-backfill state).
   */
  workspace?: string,
): Promise<DemoTrafficStatus> {
  try {
    const qs = new URLSearchParams({ project });
    if (workspace) qs.set("workspace", workspace);
    const res = await fetch(`${getApiBase()}/demo-traffic/status?${qs.toString()}`, {
      headers: apiHeaders(),
    });
    if (!res.ok) return { project, running: false };
    return (await res.json()) as DemoTrafficStatus;
  } catch {
    return { project, running: false };
  }
}

/* -------------------------------- Feedback ------------------------------- */

/** Post run feedback (POST /feedback). Returns the parsed { ok, feedback_id, error }. */
export async function postFeedback(body: FeedbackInput): Promise<FeedbackResult> {
  const res = await fetch(`${getApiBase()}/feedback`, {
    method: "POST",
    headers: apiHeaders(),
    body: JSON.stringify(body),
  });
  return res.json();
}

/* ---------------------------------- Voice ---------------------------------- */

/** A minted Gemini Live token (see dashboard_agent/voice.py). */
export interface VoiceToken {
  token: string;
  model: string;
  expires_at: string;
}

/**
 * Mint a short-lived token for a Live API session.
 *
 * The browser connects to Google directly with this instead of an API key, so
 * `GEMINI_API_KEY` never leaves the server. Short-lived and single-use: mint one per
 * session, not per app load.
 */
export async function voiceToken(): Promise<VoiceToken> {
  const res = await fetch(`${getApiBase()}/voice/token`, {
    method: "POST",
    headers: apiHeaders(),
  });
  if (!res.ok) throw await errorFrom(res);
  return res.json();
}

/**
 * Record one step of the conversation's LangSmith trace (see voice_trace.py).
 *
 * Best-effort by design and never throws: a lost span is not worth interrupting a
 * conversation for, so a failure resolves to `{}` and the caller carries on untraced.
 */
export async function voiceTrace(body: Record<string, unknown>): Promise<Record<string, unknown>> {
  try {
    const res = await fetch(`${getApiBase()}/voice/trace`, {
      method: "POST",
      headers: { ...apiHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) return {};
    return await res.json();
  } catch {
    return {};
  }
}
