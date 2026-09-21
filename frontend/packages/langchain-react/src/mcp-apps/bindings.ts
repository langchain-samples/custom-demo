/**
 * Reading MCP App bindings out of a LangGraph stream.
 *
 * This is the part a host would otherwise have to write itself, and the reason
 * it is not obvious is that the two halves arrive in two different streams.
 * Nothing here is exported from the package: a caller passes the thread and
 * gets rendered apps, and where the pieces came from is our problem.
 */

/**
 * The `ui://` document a tool opens, as the stamping middleware emits it.
 *
 * A bare URI. The mime type is fixed for every MCP App, and the resource read
 * returns the authoritative one anyway; a tool's `visibility` is enforced
 * where the host builds `allowedTools`, and cannot reach a stamp regardless,
 * since a stamp only lands on a call the model made.
 */
export type McpAppUri = string;

/** One tool call that ships a UI, with everything needed to render it. */
export interface McpAppPart {
  toolCallId: string;
  toolName: string;
  /**
   * The AI message whose tool call this is.
   *
   * The anchor for placing the app in a conversation: it belongs after the
   * turn that opened it, and that turn exists before the result does, which
   * is what lets the app mount early enough to be streamed into.
   */
  messageId: string;
  app: McpAppUri;
  /** Arguments as they stand. Changes while the model is still writing them. */
  input: Record<string, unknown>;
  /** The result, once the tool has returned. */
  output?: { content: unknown[]; structuredContent?: unknown };
  /** True while the arguments are still arriving. */
  streaming: boolean;
}

/** The thread shape this package needs, which is what `useStream` returns. */
export interface McpAppThread {
  messages?: unknown[];
  values?: unknown;
  isLoading?: boolean;
}

/**
 * The bindings carried by a values snapshot, keyed by tool call id.
 *
 * They ride in `values` and NOT in `messages`. Messages mode emits the model's
 * chunks as they are produced, and middleware that stamps the binding runs
 * after the handler, so the chunks are already gone: measured at 0 occurrences
 * in a messages stream against 3 in a values stream of the same run. A host
 * therefore subscribes to both and takes half from each, which is the one
 * thing about MCP Apps on LangGraph that is not guessable.
 */
function appsFromValues(values: unknown): Record<string, McpAppUri> {
  const messages = (values as { messages?: unknown[] } | undefined)?.messages ?? [];
  const apps: Record<string, McpAppUri> = {};
  for (const message of messages) {
    const stamped = (message as { additional_kwargs?: { mcp_app?: unknown } })
      ?.additional_kwargs?.mcp_app;
    if (stamped && typeof stamped === "object") {
      Object.assign(apps, stamped as Record<string, McpAppUri>);
    }
  }

  return apps;
}

/**
 * What the tool returned, in the shape SEP-1865 puts on the wire.
 *
 * A `ToolMessage` splits the result in two: `content` is the text blocks, and
 * `artifact.structured_content` is the structured half, under LangChain's
 * snake_case spelling of `structuredContent`. Views read the structured half,
 * so forwarding only `content` hands an app a JSON string where it expected an
 * object and every field it draws comes out `undefined`.
 */
function toolResult(message: {
  content?: unknown;
  artifact?: { structured_content?: unknown } | null;
}) {
  return {
    content: Array.isArray(message.content) ? message.content : [],
    structuredContent: message.artifact?.structured_content,
  };
}

/**
 * Every tool call in the thread that ships a UI.
 *
 * A join across the two streams: the calls and their results come from the
 * messages, the bindings from the values snapshot, and `tool_call_id` is the
 * key they share. Calls with no binding are dropped, so a thread full of
 * ordinary tools produces nothing and costs nothing.
 */
export function mcpAppParts(
  thread: McpAppThread,
  byName: Record<string, McpAppUri> = {},
): McpAppPart[] {
  const messages = (thread.messages ?? []) as Record<string, any>[];
  const stamped = appsFromValues(thread.values);

  const outputs = new Map<string, McpAppPart["output"]>();
  for (const message of messages) {
    if (message.type === "tool" && message.tool_call_id) {
      outputs.set(message.tool_call_id, toolResult(message));
    }
  }

  const parts: McpAppPart[] = [];
  for (const message of messages) {
    if (message.type !== "ai") continue;
    for (const call of (message.tool_calls ?? []) as Record<string, any>[]) {
      if (!call.id || !call.name) continue;
      // By NAME first, and that ordering is the whole reason arguments can
      // stream. A binding known up front identifies an app from the tool name
      // alone, which the model writes before it writes the arguments. The
      // per-call stamp arrives in a state snapshot emitted once the message is
      // complete, so a host that waits for it has already missed every
      // partial. The stamp stays as the fallback, for a host that has no
      // up-front map and for tools that appear without one.
      const app = byName[call.name] ?? stamped[call.id];
      if (!app) continue;

      const output = outputs.get(call.id);
      parts.push({
        toolCallId: call.id,
        toolName: call.name,
        messageId: String(message.id ?? ""),
        app,
        input: (call.args ?? {}) as Record<string, unknown>,
        output,
        // A call whose result has not arrived while the run is still going is
        // a call the model may still be writing. Once the result is in, the
        // arguments are settled whatever the run is doing.
        streaming: output === undefined && thread.isLoading === true,
      });
    }
  }

  return parts;
}
