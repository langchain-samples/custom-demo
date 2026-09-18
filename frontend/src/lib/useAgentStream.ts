/**
 * `useStream`, configured the way this deployment needs it.
 *
 * It owns the RUN: creating it, streaming it, `isLoading`, `stop()`, the error
 * it failed with, `thread.values`, and `thread.interrupts` - which is how a
 * tool pausing for human review reaches the UI.
 *
 * It does NOT own the rendered transcript. `thread.messages` stays EMPTY here
 * by design, so do not reach for it: the SDK builds that array from
 * `messages-tuple` frames, which this deployment does not request, and
 * `agentTransport` says why in the comment on `STREAM_MODES`. The chat
 * transcript, tool chips, widgets, artifacts and subagent cards are all built
 * in `ChatPanel` from the cumulative `messages/*` frames the transport hands to
 * `onFrame`.
 *
 * A custom `transport` rather than `apiUrl` for two reasons, both in
 * `agentTransport`: a turn's request carries things `SubmitOptions` cannot
 * express (a `rubric` on the input, per-run tracing headers), and the SDK's own
 * subagent routing misfiles this graph's main-agent tool results.
 */
import { useMemo, useRef } from "react";
import { useStream } from "@langchain/langgraph-sdk/react";
import { agentTransport, type AgentFrame } from "@/lib/agentTransport";
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
  /** The assistant a turn runs against, read at submit time. */
  assistantId: string;
  /**
   * Extra headers for the turn being submitted, for a voice turn's tracing
   * parent. A function because a turn sets them and submits in the same tick:
   * a plain value would be read from the last render and so always one turn
   * behind.
   */
  getHeaders: () => Record<string, string> | undefined;
  /** Every frame of the run, subagent frames included, in arrival order. */
  onFrame: (frame: AgentFrame) => void;
}

/**
 * The stream for this SPA's turns.
 *
 * All three options are read through refs at stream time, not captured, so a
 * turn always uses the assistant, headers and frame handler that are current
 * when it is submitted. The transport is therefore built once: rebuilding it
 * would hand `useStream` a new transport identity on every render.
 */
export function useAgentStream(options: AgentStreamOptions) {
  const live = useRef(options);
  live.current = options;

  const transport = useMemo(
    () =>
      agentTransport({
        assistantId: () => live.current.assistantId,
        headers: () => live.current.getHeaders(),
        onFrame: (frame) => live.current.onFrame(frame),
      }),
    [],
  );

  return useStream<AgentState>({ transport, messagesKey: "messages" });
}
