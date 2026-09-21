/**
 * Finding the MCP Apps in a LangGraph thread.
 *
 * Nothing here is exported from the package: a caller passes the thread and
 * the map of which tools ship a UI, and gets back the calls worth rendering.
 */

/**
 * The `ui://` document a tool opens, as the stamping middleware emits it.
 *
 * A bare URI. The mime type is fixed for every MCP App, and the resource read
 * returns the authoritative one anyway; a tool's `visibility` is enforced
 * on the route that proxies a view's call.
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
  /** The `ui://` document this call opens. */
  uri: McpAppUri;
  /** Arguments as they stand. Changes while the model is still writing them. */
  input: Record<string, unknown>;
  /** The result, once the tool has returned. */
  output?: { content: unknown[]; structuredContent?: unknown };
  /** True while the arguments are still arriving. */
  streaming: boolean;
}

/**
 * The thread shape this package needs, which is what `useStream` returns.
 *
 * `messages` only. A host subscribing to `stream_mode: "messages"` has
 * everything the renderer wants, because which tools ship a UI is answered
 * once by the host's own route rather than carried on each call.
 */
export interface McpAppThread {
  messages?: unknown[];
  isLoading?: boolean;
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
 * The calls and their results come from the messages, and `apps` says which
 * tool names ship a UI. Calls with no binding are dropped, so a thread full of
 * ordinary tools produces nothing and costs nothing.
 */
export function mcpAppParts(
  thread: McpAppThread,
  appUris: Record<string, McpAppUri> = {},
): McpAppPart[] {
  const messages = (thread.messages ?? []) as Record<string, any>[];

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
      // By NAME, which is what lets arguments stream. A map known up front
      // identifies an app from the tool name alone, and the model writes the
      // name before it writes the arguments. Anything carried on the call
      // itself arrives too late to be useful: LangGraph puts a message's
      // `additional_kwargs` in a state snapshot emitted once the message is
      // COMPLETE, by which point every partial has already gone past.
      const uri = appUris[call.name];
      if (!uri) continue;

      const output = outputs.get(call.id);
      parts.push({
        toolCallId: call.id,
        toolName: call.name,
        messageId: String(message.id ?? ""),
        uri,
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
