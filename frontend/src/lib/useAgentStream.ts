/**
 * `useStream`, configured the way this deployment needs it.
 *
 * One place for the options every turn shares, so the component running a
 * turn is left with the part that is actually about this product.
 *
 * Three of these are not defaults and are easy to lose:
 *
 * `streamSubgraphs` is what makes a dispatched subagent's work visible at
 * all. Without it a subagent runs silently and the UI has nothing to show
 * between the tool call and its result, which on a long task reads as a hang.
 *
 * `custom` in `streamMode` is the channel `RubricMiddleware` grades on. Drop
 * it and the goal verdict never arrives, silently, because nothing errors.
 *
 * `updates` carries `__interrupt__`, which is how a tool pausing for human
 * review reaches the UI at all.
 */
import { useStream } from "@langchain/langgraph-sdk/react";
import { apiHeaders, getApiBase, getApiKey, getAssistantId } from "@/lib/config";
import type { ThreadMessage } from "@/lib/api";

/** The agent's state, as the SPA reads it. */
export interface AgentState extends Record<string, unknown> {
  messages: ThreadMessage[];
  /**
   * The active goal's rubric.
   *
   * Agent state rather than context on purpose: the middleware compares it to
   * the previous turn's to decide whether this is the same grading run or a
   * new one, so it has to be checkpointed alongside the messages.
   */
  rubric: string;
}

export interface AgentStreamOptions {
  threadId: string | null;
  onThreadId: (id: string) => void;
}

/**
 * The stream for one thread.
 *
 * `apiKey` and `defaultHeaders` are both supplied: the key is what the
 * deployment authenticates on, and the headers carry whatever else
 * `apiHeaders` adds, which includes the shared-secret header when one is set.
 */
export function useAgentStream({ threadId, onThreadId }: AgentStreamOptions) {
  return useStream<AgentState>({
    apiUrl: getApiBase(),
    apiKey: getApiKey() || undefined,
    defaultHeaders: apiHeaders(),
    assistantId: getAssistantId(),
    threadId,
    onThreadId,
    messagesKey: "messages",
  });
}

/**
 * The per-run options, which is where the stream modes actually live.
 *
 * NOT hook options: `useStream` takes the connection, and `submit` takes how
 * a given run should stream. Spread this into every `submit` so a turn cannot
 * silently lose a channel.
 */
export const RUN_STREAM_OPTIONS = {
  // "messages" is the token stream the chat and widgets are built from;
  // "updates" carries `__interrupt__`; "custom" carries the goal verdict.
  streamMode: ["messages", "updates", "custom"],
  streamSubgraphs: true,
} as const;
