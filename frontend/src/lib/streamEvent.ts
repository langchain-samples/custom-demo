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
