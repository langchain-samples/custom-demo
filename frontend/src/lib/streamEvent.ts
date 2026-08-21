/**
 * Pure, dependency-free helpers for routing streamed run frames by their
 * subgraph namespace. Kept free of React / api imports so the repo can Node-test
 * it directly (see dashboard_agent/tests/stream_event_test.js, which imports this
 * real .ts via Node type-stripping — the same pattern as chart_test.js).
 *
 * When the run is streamed with `stream_subgraphs: true`, the LangGraph server
 * appends the emitting subgraph's checkpoint namespace to the SSE event name with
 * `|` separators:
 *
 *   "messages/partial"                     -> main graph        (namespace [])
 *   "messages/partial|tools:abc"           -> a task subagent   (["tools:abc"])
 *   "messages/partial|tools:abc|model:def" -> deeper in that subagent
 *
 * The first path segment (`tools:<task_call_id>`) identifies the observable,
 * task-dispatched subagent instance; deeper segments are its internal graph
 * nodes (model_request / tools) or nested subagents.
 */

/** An SSE event name split into its base event and subgraph namespace path. */
export interface SplitEvent {
  event: string;
  namespace: string[];
}

/**
 * Split a raw SSE event name on `|`: the head is the base event, the tail is the
 * subgraph namespace path. Empty segments are dropped so a stray trailing `|`
 * can't manufacture a phantom namespace.
 */
export function splitStreamEvent(raw: string): SplitEvent {
  const parts = (raw || "").split("|");
  return { event: parts[0] || "", namespace: parts.slice(1).filter(Boolean) };
}

/**
 * Parse a `langgraph_checkpoint_ns` string ("tools:abc|model_request:def") into
 * its path segments — the fallback source of a frame's namespace on server
 * versions that surface it in messages/metadata rather than the event name.
 */
export function parseCheckpointNs(cns: string | null | undefined): string[] {
  if (!cns) return [];
  return cns.split("|").filter(Boolean);
}

/**
 * True when a namespace belongs to an observable (task-dispatched) subagent —
 * i.e. it is rooted at a `tools:*` subgraph. This is the ONLY reliable
 * main-vs-subagent discriminator: an empty namespace is the main graph, but the
 * main agent's OWN internal nodes also carry a NON-empty checkpoint namespace
 * (e.g. `model_request:…`) on the deployed server, so routing must key on the
 * `tools:` prefix — not on "non-empty" — or the whole main answer is misrouted
 * into a subagent card.
 */
export function isSubagentNamespace(ns: string[]): boolean {
  return ns.length > 0 && ns[0].startsWith("tools:");
}

/** A subagent instance's stable bucket key and human-readable label. */
export interface SubagentIdentity {
  key: string;
  label: string;
}

/**
 * Identify the subagent instance a namespace belongs to.
 *
 * Keyed by the first path segment (`tools:<call_id>`) PLUS any trailing NUMERIC
 * segments. Two cases share the `tools:` root but mean different things:
 *
 *   - A `task`-tool subagent streams messages under `tools:abc` and its internal
 *     nodes under `tools:abc|model:def` — those NAMED (colon-bearing) deeper
 *     segments are internal to ONE subagent and must roll up into one card.
 *   - `task()`-from-code (eval) dispatches run in PARALLEL and are distinguished
 *     only by a trailing numeric branch: `tools:abc`, `tools:abc|1`, `tools:abc|2`.
 *     Those are separate subagent instances and each gets its own card.
 *
 * So we keep numeric branch segments in the key and drop named node segments.
 * The label reads nicely — the raw "tools" dispatch node becomes "Subagent".
 */
export function subagentIdentity(ns: string[]): SubagentIdentity {
  const head = ns[0] || "";
  const prefix = head.split(":")[0] || "subagent";
  const label = prefix === "tools" ? "Subagent" : prefix;
  const branches = ns.slice(1).filter((s) => /^\d+$/.test(s));
  const key = [head || "subagent", ...branches].join("|");
  return { key, label };
}

/**
 * The launching call's root for a subagent bucket key: the `tools:<call_id>`
 * head, with any trailing numeric branch dropped. Every parallel `task()`
 * dispatch from ONE `eval` shares this root (they differ only by branch:
 * `tools:abc`, `tools:abc|1`, `tools:abc|2`), so grouping cards by it collapses a
 * whole fanned-out fleet into one group. A lone `task`-tool subagent is its own
 * root (a group of one).
 */
export function subagentRoot(key: string): string {
  return (key || "").split("|")[0] || "";
}

/**
 * Resolve the effective namespace for a streamed message frame: prefer the
 * namespace parsed off the event name; if that is empty (older servers), fall
 * back to a checkpoint namespace previously recorded for this message id.
 */
export function effectiveNamespace(
  eventNs: string[],
  messageId: string | undefined,
  nsById: Record<string, string[]>,
): string[] {
  if (eventNs.length) return eventNs;
  if (messageId && nsById[messageId]) return nsById[messageId];
  return [];
}

/**
 * Which parallel dispatch a subagent bucket key is: the last numeric branch
 * segment, or 0 when there is none. One `eval` that fans out lands as
 * `tools:abc` (0), `tools:abc|1`, `tools:abc|2`, …
 */
export function taskBranch(key: string): number {
  const numeric = (key || "")
    .split("|")
    .slice(1)
    .filter((s) => /^\d+$/.test(s));
  return numeric.length ? Number(numeric[numeric.length - 1]) : 0;
}

/** What one `task()` call in an interpreter script asked for. */
export interface TaskDispatch {
  /** The named subagent it was routed to ("analyst", "researcher"). */
  subagentType: string;
  /** The instruction it was dispatched with. */
  description: string;
}

/** `${...}` interpolations carry code, not information a viewer wants. */
function cleanDescription(raw: string): string {
  return raw
    .replace(/\$\{[^}]*\}/g, "…")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * Pull the `task({subagentType, description})` dispatches out of an interpreter
 * script.
 *
 * A subagent dispatched by the `task` TOOL announces itself in the tool call's
 * args, so its card can name it. One dispatched from a JS workflow script — which
 * is what the dynamic-subagent skills tell the agent to write — arrives as a bare
 * `tools:<eval_id>` namespace with no args anywhere in the stream: the who and the
 * what are inside the source the interpreter ran. Reading them back out of that
 * source is the only way those cards can say more than "Subagent".
 *
 * Deliberately a scan and not a parse: this runs on partially-streamed source, so
 * anything stricter would have to fail on every frame until the script completes.
 * A call whose braces are still arriving simply doesn't match yet.
 */
export function parseTaskDispatches(code: string): TaskDispatch[] {
  const out: TaskDispatch[] = [];
  if (!code) return out;
  const calls = [...code.matchAll(/\btask\s*\(\s*\{/g)];
  for (let i = 0; i < calls.length; i++) {
    const from = (calls[i].index ?? 0) + calls[i][0].length;
    // Bounded window: a dispatch's own fields, never the next call's — a script
    // whose first dispatch omits a field must not borrow it from the second.
    const next = calls[i + 1]?.index ?? code.length;
    const body = code.slice(from, Math.min(next, from + 1200));
    const type = /subagent_?[tT]ype\s*:\s*(["'`])([^"'`]*)\1/.exec(body);
    const desc = /description\s*:\s*(["'`])([\s\S]*?)\1/.exec(body);
    if (!type && !desc) continue;
    out.push({
      subagentType: (type?.[2] || "").trim(),
      description: cleanDescription(desc?.[2] || ""),
    });
  }
  return out;
}
