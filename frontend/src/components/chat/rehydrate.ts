/**
 * Rebuilding a conversation from its persisted messages, on a page refresh.
 *
 * The live path builds items from streamed frames; this builds the same items
 * from `GET /threads/{id}/state`. Deliberately a subset: the user's turns, the
 * assistant's answers, and any MCP App. The activity trace, subagent cards and
 * dashboard widgets are not reconstructed, because they describe a run in
 * progress rather than what the conversation says, and a half-replayed trace
 * reads worse than none.
 *
 * Kept pure and separate from `ChatPanel` so the mapping is testable without a
 * DOM: the interesting cases are all about which message becomes which item.
 */
import type { McpAppBinding, MessageContent, ThreadMessage } from "@/lib/api";
import { contentToText } from "@/components/chat/helpers";

/** A tool result's structured half, when the text is a JSON object. */
export function parseStructured(text: string): Record<string, unknown> | undefined {
  if (!text.trim().startsWith("{")) return undefined;
  try {
    const parsed = JSON.parse(text) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : undefined;
  } catch {
    return undefined;
  }
}

/**
 * The `structuredContent` an MCP App should be handed for a tool result.
 *
 * `langchain.mcp` does NOT lose it: `_convert_call_tool_result` puts it on the
 * ToolMessage as `MCPToolArtifact(structured_content=...)`, which is the
 * content-and-artifact idiom. Reading the artifact is therefore the correct
 * source, and the only one that is right when a server's `content` text
 * deliberately differs from its `structuredContent` (the spec's own advice:
 * text for the model, structured for the UI).
 *
 * Parsing the text is the fallback, for a server that sends no structured half
 * and a result whose text happens to be the JSON the app wants.
 */
export function structuredFromToolMessage(msg: ThreadMessage): Record<string, unknown> | undefined {
  const artifact = (msg as { artifact?: { structured_content?: unknown } }).artifact;
  const structured = artifact?.structured_content;
  if (structured && typeof structured === "object" && !Array.isArray(structured)) {
    return structured as Record<string, unknown>;
  }

  return parseStructured(contentToText(msg.content as MessageContent | undefined));
}

/** The subset of chat items a persisted conversation can be rebuilt into. */
export type RestoredItem =
  | { kind: "user"; id: string; text: string }
  | { kind: "assistant"; id: string; text: string; streaming: false; markdown: true }
  | {
      kind: "app";
      id: string;
      toolName: string;
      toolArgs: Record<string, unknown>;
      streaming: false;
      toolResult?: { structuredContent?: unknown; content?: unknown[] };
    };

/**
 * Turn a thread's messages back into chat items.
 *
 * `apps` is the bootstrap map, and it is what makes this possible at all: a
 * finished tool call in history carries no UI metadata, so without the map
 * there is no way to tell which of them should come back as an app. The old
 * prefix guess could not have done this correctly.
 */
export function rehydrateItems(
  messages: ThreadMessage[],
  apps: Record<string, McpAppBinding>,
): RestoredItem[] {
  const items: RestoredItem[] = [];
  // Apps by tool_call_id, so the ToolMessage that arrives later can attach its
  // result to the card the AI message opened.
  const appByCall = new Map<string, RestoredItem & { kind: "app" }>();

  messages.forEach((msg, i) => {
    const type = msg.type ?? msg.role ?? "";
    const text = contentToText(msg.content).trim();
    const id = msg.id || `restored:${i}`;

    if (type === "human" || type === "user") {
      if (text) items.push({ kind: "user", id, text });
      return;
    }

    if (type === "ai" || type === "assistant") {
      // Narration first: the model writes it before it calls anything, and the
      // card belongs under the sentence that introduced it.
      if (text) items.push({ kind: "assistant", id, text, streaming: false, markdown: true });
      for (const call of msg.tool_calls ?? []) {
        const name = call.name ?? "";
        if (!call.id || !apps[name]) continue;

        const item = {
          kind: "app" as const,
          id: `app:${call.id}`,
          toolName: name,
          toolArgs: (call.args ?? {}) as Record<string, unknown>,
          streaming: false as const,
        };
        appByCall.set(call.id, item);
        items.push(item);
      }

      return;
    }

    if (type === "tool" && msg.tool_call_id) {
      const item = appByCall.get(msg.tool_call_id);
      // A result with no app is an ordinary tool call, and has no card to
      // attach to. Mutating in place keeps the card where the AI message put
      // it, rather than moving it after its own result.
      if (item) item.toolResult = { structuredContent: structuredFromToolMessage(msg), content: [] };
    }
  });

  return items;
}

/**
 * Whether a `resetKey` change means "the person switched assistant or asked
 * for a new chat", as opposed to "the assistant finally loaded".
 *
 * The key is `<assistantId>:<counter>`, and the id is empty until the
 * assistant list arrives, so every page load produces one change from `":0"`
 * to `"<uuid>:0"` a tick after mount. Treating that as a switch threw the
 * thread away on load: the conversation was restored and then immediately
 * cleared, which looked exactly like persistence not working.
 */
export function isDeliberateReset(previous: string, next: string): boolean {
  if (previous === next) return false;
  const [wasAssistant, wasCounter] = previous.split(":");
  const [nowAssistant, nowCounter] = next.split(":");
  // The assistant arriving for the first time, with nothing else changed.
  if (!wasAssistant && nowAssistant && wasCounter === nowCounter) return false;
  return true;
}
