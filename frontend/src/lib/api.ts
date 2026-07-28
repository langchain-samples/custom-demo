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
 *   POST   /feedback
 */
import { GRAPH_ID, apiHeaders, getApiBase } from "./config";

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
  [key: string]: unknown;
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
  /** Capabilities the new assistant starts with; editable afterwards. */
  enabled_tools?: string[];
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
   */
  messages?: Array<{ role: string; content: string }>;
  /** Per-run runtime context; omitted from the body when empty. */
  context?: RunContext;
  /** Resume a run paused at an interrupt, with the human's reviewed value. */
  resume?: unknown;
  /** Optional abort signal to cancel the stream. */
  signal?: AbortSignal;
}

/**
 * Stream a run over SSE (stream_mode:"messages") and yield each decoded
 * `{ event, data }` block. Framing is CRLF-normalized (\r stripped) so
 * `\r\n\r\n` boundaries parse as `\n\n`, matching the original reader. `data`
 * is the raw string (typically JSON) — the caller parses it.
 */
export async function* runStream(opts: RunStreamOptions): AsyncGenerator<SSEEvent> {
  const { threadId, assistantId, messages, context, resume, signal } = opts;
  const body: Record<string, unknown> = {
    assistant_id: assistantId,
    // "updates" carries `__interrupt__` when a tool pauses for human review;
    // "messages" is the token stream the chat + widgets are built from.
    stream_mode: ["messages", "updates"],
  };
  if (resume !== undefined) {
    // A resume replaces input entirely — sending both would duplicate the turn.
    body.command = { resume };
  } else {
    body.input = { messages: messages || [] };
  }
  if (context && Object.keys(context).length) body.context = context;

  const res = await fetch(`${getApiBase()}/threads/${threadId}/runs/stream`, {
    method: "POST",
    headers: apiHeaders(),
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
      if (dataLines.length) yield { event, data: dataLines.join("\n") };
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
