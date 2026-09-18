/**
 * The `useStream` transport for this deployment.
 *
 * `useStream` can be handed a transport instead of an `apiUrl`, and that is the
 * shape this SPA needs for two reasons.
 *
 * The request is the first. A turn here is not a bare `{messages}`: it carries a
 * `rubric` on the input, a per-run `context`, and - on a voice-driven turn -
 * LangSmith distributed-tracing headers that make the run nest under the
 * conversation's tool span. `SubmitOptions` has nowhere to put per-run headers,
 * so `api.runStream` stays the one place that builds the body, and this wraps it.
 *
 * The second is that the SDK's own subagent routing is wrong on this graph, so
 * this filters subagent frames out before the SDK sees them and hands them to
 * `onFrame` instead.
 *
 * Measured against `langgraph dev` on this agent, a MAIN-agent tool result
 * arrives as:
 *
 *   event "messages/metadata"  namespace []  node "tools"
 *                              langgraph_checkpoint_ns "tools:517cf673-..."
 *
 * The event-name namespace is empty, correctly saying "main graph". But the
 * checkpoint namespace contains a `tools:` segment, because the main agent's own
 * tool-dispatch node is called `tools` and pregel names its task that way. The
 * SDK tests the event namespace first and then FALLS BACK to the checkpoint
 * namespace (`@langchain/langgraph-sdk` 1.11.0, in the stream manager's
 * `isSubagentNamespace` test on the messages event), so with
 * `filterSubagentMessages` on it files every main-agent tool
 * result under a phantom subagent keyed by that pregel task id and drops it from
 * `messages`: tool chips would never get their results. With the flag off there
 * is no filtering at all and a real subagent's model output lands in the main
 * message list, which is the other half of the same bug - it would be read out
 * as the answer.
 *
 * So the discriminator is the EVENT-NAME namespace only, which is what
 * `isSubagentNamespace` keys on and what this yields against. The SDK then owns
 * exactly the part it gets right: run creation, message accumulation,
 * interrupts, `isLoading`, `stop` and errors, all for the main graph.
 */
import type { RunContext } from "@/lib/api";
import { ensureThread, runStream } from "@/lib/api";
import { isMiddlewareNamespace, isSubagentNamespace } from "@/lib/streamEvent";

/**
 * The stream modes a turn requests here: the three this SPA has always read,
 * plus `values`.
 *
 * `values` is the addition, and it is what `useStream` needs: it is the ONLY
 * channel the SDK reads `__interrupt__` off, so a tool pausing for human review
 * reaches `thread.interrupts` from here and nowhere else.
 *
 * `messages-tuple` is deliberately NOT requested. It is the mode the SDK
 * accumulates `thread.messages` from, so leaving it out is why that array stays
 * empty (see `useAgentStream`).
 *
 * The two modes are different wire shapes for the same tokens, measured against
 * this agent on `langgraph dev`:
 *
 *   messages        cumulative. `messages/partial` carries the whole message so
 *                   far, so frame N already contains frames 1..N-1.
 *   messages-tuple  deltas. One `messages` frame per chunk, whose data is
 *                   `[AIMessageChunk, metadata]` holding only that chunk's
 *                   content and `tool_call_chunks` - partial JSON arg strings.
 *
 * Every handler downstream of `onFrame` reads a cumulative message, including
 * the ones that key a widget or a chip off a parsed `tool_call.args`. Feeding
 * them deltas would need a chunk accumulator, and the SDK's own accumulator is
 * not reusable for the half that matters: a subagent dispatched by `task()`
 * from inside an `eval` script has no `task` TOOL CALL to register against, so
 * `SubagentManager` never sees it and its card would go blank. So the tokens
 * stay cumulative and the SDK owns the run rather than the render model.
 */
export const STREAM_MODES = ["messages", "updates", "custom", "values"];

/** One streamed frame, parsed, with the namespace its event name carried. */
export interface AgentFrame {
  event: string;
  data: unknown;
  namespace: string[];
}

/** The run input this agent takes: a user turn plus the sticky goal rubric. */
export interface AgentInput {
  messages?: Array<{ role: string; content: string | Array<Record<string, unknown>> }>;
  rubric?: string;
}

/**
 * What the transport needs that a submit payload cannot carry.
 *
 * All three are read at stream time rather than captured, so the component can
 * keep them in refs and a turn always uses the CURRENT assistant, headers and
 * frame handler - not whichever ones existed when the hook first rendered.
 */
export interface AgentTransportEnv {
  assistantId: () => string;
  /** Extra headers for this run, for a voice turn's tracing parent. */
  headers: () => Record<string, string> | undefined;
  /** Every frame of the run, subagent frames included, in arrival order. */
  onFrame: (frame: AgentFrame) => void;
}

/**
 * True when a frame is the main graph's to handle.
 *
 * Root-only for `metadata`, `custom` and `error`: a subagent must not hijack the
 * run id the feedback row rates, finish the user's goal, or put its own failure
 * in the main answer bubble.
 */
function isRootFrame(frame: AgentFrame): boolean {
  return !isSubagentNamespace(frame.namespace) && !isMiddlewareNamespace(frame.namespace);
}

/** Build the `transport` for `useStream`. */
export function agentTransport(env: AgentTransportEnv) {
  return {
    /**
     * `payload` is typed the loose way `useStream` declares it - the state type
     * it threads through is `Partial<State> & Record<string, unknown>`, which
     * no narrower annotation here will accept - so the two fields this reads
     * are narrowed on the way out.
     */
    async stream(payload: {
      input?: Record<string, unknown> | null;
      context?: Record<string, unknown>;
      command?: { resume?: unknown };
      signal: AbortSignal;
    }) {
      const input = (payload.input ?? {}) as AgentInput;
      const threadId = await ensureThread();
      const frames = runStream({
        threadId,
        assistantId: env.assistantId(),
        streamMode: STREAM_MODES,
        ...(payload.command && "resume" in payload.command
          ? { resume: payload.command.resume }
          : { messages: input.messages || [] }),
        rubric: input.rubric,
        context: payload.context as RunContext | undefined,
        headers: env.headers(),
        signal: payload.signal,
      });

      return (async function* () {
        for await (const { event, data, namespace } of frames) {
          let parsed: unknown;
          try {
            parsed = JSON.parse(data);
          } catch {
            // A frame whose data is not JSON carries nothing either half can
            // read. Dropping it is what the hand-rolled loop did too.
            continue;
          }

          const frame: AgentFrame = { event, data: parsed, namespace };
          env.onFrame(frame);
          // A middleware's private model call (the goal grader) is neither the
          // main graph nor a subagent, and its frames are AI messages with no
          // tool calls - the exact shape of a final answer. It reaches
          // `onFrame` above, where the rubric verdict is read off the `custom`
          // channel, and stops here so the SDK never accumulates the verdict
          // JSON as the assistant's reply.
          if (isRootFrame(frame)) yield { event, data: parsed };
        }
      })();
    },
  };
}
