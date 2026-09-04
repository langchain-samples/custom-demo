/**
 * ChatPanel — the left rail: message list, streaming answer bubble, tool-activity
 * chips, thumbs feedback, quick-prompt presets and the input form.
 *
 * It owns the streaming loop (api.runStream) and the onMessage handler. The
 * metadata `langgraph_node === "model"` guard ensures only the MAIN agent's final
 * answer renders in the bubble — AI messages emitted from inside a tool (the
 * synthetic data source's own LLM call, tagged with a different node) never leak
 * into the chat. push_widget args are emitted to `onWidget` with the original
 * progressive-flush logic (flush each widget when the next begins; flush the last
 * at stream end) so charts appear as they complete but never half-built.
 *
 * Routing/guards (assistant / workspace / system-prompt requirements) live in App;
 * this component only calls the `guard` prop before sending.
 */
import { useEffect, useImperativeHandle, useRef, useState } from "react";
import type { ReactNode, Ref } from "react";
import { Streamdown } from "streamdown";
import {
  IconAlertTriangle,
  IconCircleCheck,
  IconFileText,
  IconLoader2,
  IconPaperclip,
  IconRobot,
  IconTarget,
  IconUser,
} from "@tabler/icons-react";
import type { QuickAction, ReviewInterrupt, RunContext, ThreadMessage, Widget } from "@/lib/api";
import { ensureThread, resetThread, runStream } from "@/lib/api";
import { PROSE_CLS } from "@/lib/markdown";
import { isHtmlArtifactPath } from "@/lib/artifacts";
import { ReviewCard } from "@/components/chat/ReviewCard";
import { Button } from "@/components/motion/button";
import { ToolChip, type ChipData } from "@/components/chat/ToolChip";
import { ToolChipGroup } from "@/components/chat/ToolChipGroup";
import type { GraphSubagent } from "@/lib/agentGraph";
import { FeedbackRow } from "@/components/chat/FeedbackRow";
import { IconChevronDown, IconChevronRight } from "@tabler/icons-react";
// beUI agent surface (copy-paste registry components under @/components/agents).
import { MessageBubble, MessageBubbleContent } from "@/components/agents/message-bubble";
import { StreamingResponse } from "@/components/agents/streaming-response";
import { ReasoningText } from "@/components/agents/loading-states/reasoning-text";
import { PromptInput } from "@/components/agents/prompt-input";
import {
  chipArgSummary,
  chipCode,
  contentToText,
  widgetFromArgs,
  widgetLooksComplete,
  describeInterrupt,
} from "@/components/chat/helpers";
import {
  IMAGE_MIME_TYPES,
  imageContent,
  readImageAttachment,
  uploadSandboxFiles,
  type ImageAttachment,
  type SandboxTarget,
} from "@/lib/api";
import { COMMANDS, parseGoalCommand, type GoalCommand } from "@/lib/commands";
import {
  effectiveNamespace,
  isMiddlewareNamespace,
  isSubagentNamespace,
  parseCheckpointNs,
  parseTaskDispatches,
  subagentIdentity,
  subagentRoot,
  taskBranch,
  type TaskDispatch,
} from "@/lib/streamEvent";

/**
 * What one turn produced, for a programmatic caller (voice mode). The chat panel and
 * dashboard have already rendered it; this is only what needs SPEAKING.
 */
export interface TurnResult {
  /** The final prose. Never widget JSON: the figures are on screen. */
  answer: string;
  /** Widgets this turn flushed, so a caller can read out the headline figures. */
  widgets: Widget[];
  /**
   * Set when the run PAUSED for a human instead of finishing: a one-line description
   * of the question plus its options, for reading aloud.
   */
  approval?: string;
  error?: string;
  /**
   * The agent run's LangSmith id. Voice mode records it on its `invoke_deep_agent` span,
   * which is how the conversation trace and the agent trace stay connected (they cannot be
   * nested - see the note in graph.py).
   */
  runId?: string;
}

/**
 * The imperative surface voice mode drives. `ask` is the same code path the composer
 * uses, so a spoken question produces the same widgets, chips, transcript and trace as a
 * typed one - which is the entire reason voice mode runs the agent from the browser.
 */
export interface ChatPanelHandle {
  ask(
    question: string,
    headers?: Record<string, string>,
    onProgress?: (toolName: string) => void,
  ): Promise<TurnResult>;
  /** Answer whatever the last turn paused on. */
  resumeWith(value: unknown): Promise<TurnResult>;
  busy(): boolean;
}

export interface ChatPanelProps {
  /**
   * The voice control, rendered in the composer beside send. A ReactNode rather than the
   * session itself: the composer is the only thing here that needs to know voice exists,
   * and App already owns the session.
   */
  voiceControl?: ReactNode;
  /** Imperative handle for voice mode (see ChatPanelHandle). */
  handleRef?: Ref<ChatPanelHandle>;
  /** The assistant id to run against (a UUID once one is selected in settings). */
  assistantId: string;
  /** Quick-action presets rendered under "Try a quick prompt". */
  presets?: QuickAction[];
  /**
   * Builds the per-run runtime context at send time (prompt/prompt_name,
   * data_prompt, data_gap, ls_workspace, ls_project). Called fresh on every send
   * so it reflects the latest settings. An empty object is sent as no context.
   */
  getRunContext: () => RunContext;
  /** Receives each widget as it flushes (progressive), for the dashboard pane.
   *  Widgets accumulate across turns; the dashboard is cleared only by New Chat. */
  onWidget: (widget: Widget) => void;
  /**
   * Receives an HTML artifact as `write_file` streams it into /workspace/artifacts.
   *
   * `content` is the partial document while `streaming` is true, which is what makes
   * the artifact tab build up on screen as the agent types it. Once the write
   * completes (`streaming` false) App re-reads the file from the sandbox, because an
   * `edit_file` follow-up carries only a diff and never the whole document.
   */
  onArtifact?: (artifact: {
    path: string;
    content: string;
    streaming: boolean;
    /** The agent deleted the file: drop the tab rather than leave it blank. */
    deleted?: boolean;
  }) => void;
  /**
   * Send guard, run before every send. Return an error string to BLOCK the send
   * (rendered as an assistant message; App opens settings as a side effect) or
   * null/undefined to allow it.
   */
  guard?: (question: string) => string | null | undefined;
  /**
   * Change this to reset the conversation (assistant switch / new chat): the
   * message log clears, any in-flight stream aborts, and the next send mints a
   * fresh server thread.
   */
  resetKey?: string | number;
  /** Active assistant branding for the empty-state (shown before any messages). */
  logo?: string;
  /** Active assistant industry — used in the hero input placeholder. */
  industry?: string;
  /** Whether a real assistant is selected — drives the empty-state (CTA vs branded). */
  hasAssistant?: boolean;
  /**
   * False until the assistant list has loaded. Without it there is no way to tell "no
   * assistant" from "not asked yet", so the empty state was shown to everyone during
   * the first fetch, including people who do have assistants.
   */
  assistantsLoaded?: boolean;
  /** Open the settings sheet (from the "Choose assistant" empty-state CTA). */
  onOpenSettings?: () => void;
  /**
   * Which assistant's VM a dropped document belongs in — the same key the Files
   * dialog uses. Absent before an assistant is chosen, in which case a dropped
   * document has nowhere to go and the drop is refused with a reason.
   */
  sandboxTarget?: SandboxTarget;
  /**
   * Mirrors the current turn's tool chips and subagent groups out to the parent so the
   * Graph tab (`AgentGraph`) can draw them. Read-only: the callback never feeds anything
   * back in, so omitting it leaves ChatPanel behaving exactly as before.
   */
  onActivity?: (a: {
    chips: ChipData[];
    subagents: GraphSubagent[];
    running: boolean;
  }) => void;
}

/* ---- Internal message-list model ---- */

interface UserItem {
  kind: "user";
  id: string;
  text: string;
  /** Data URLs for images sent with this turn, so the log shows what the agent saw. */
  images?: string[];
  /** Documents uploaded to the VM for this turn, so the log shows what the agent got. */
  docs?: { name: string; path: string }[];
}
interface ActivityItem {
  kind: "activity";
  id: string;
  chips: ChipData[];
}
/** One task-dispatched subagent instance's live work (its own chips + text). */
interface SubagentGroup {
  /** subagentIdentity(ns).key — the top-level `tools:<call_id>` segment. */
  key: string;
  /** Human-readable name: the subagent's type ("Analyst"), else "Subagent". */
  label: string;
  /** The registered subagent it was routed to ("analyst", "researcher"), if known. */
  type?: string;
  chips: ChipData[];
  /** The subagent's own streamed text (non-tool AI output), if any. */
  text: string;
  /** What the parent dispatched this subagent with (the `task` description). */
  invokedWith?: string;
  /** Frozen true once the run ends (finally block) — stops the spinner. */
  done: boolean;
}
/**
 * The "peer into subagents" panel for a turn: a collapsible card per observable
 * (task-dispatched) subagent. Rendered distinct from the main answer bubble;
 * subagent work NEVER feeds the main answer/widgets/chips.
 */
interface SubagentItem {
  kind: "subagents";
  id: string;
  groups: SubagentGroup[];
}
interface AssistantItem {
  kind: "assistant";
  id: string;
  text: string;
  streaming: boolean;
  /** When true the text is rendered as markdown (final answer); else plain. */
  markdown: boolean;
}
interface FeedbackItem {
  kind: "feedback";
  id: string;
  runId: string;
  /** Workspace the run traced to — feedback must target the same tenant. */
  workspace?: string;
}
/** A tool paused the run for human review; resolved by resuming the thread. */
interface ReviewItem {
  kind: "review";
  id: string;
  review: ReviewInterrupt;
  /** Cleared once approved, so the editor collapses to a read-only card. */
  done: boolean;
}
type Item = UserItem | ActivityItem | SubagentItem | AssistantItem | FeedbackItem | ReviewItem;

/* ------------------------------- Goals ---------------------------------- */

/**
 * A `/goal`: what the user is working towards, graded by `RubricMiddleware`.
 *
 * `text` goes over the wire as the run's `rubric`. `status` is UI-only, driven by
 * the grader's `rubric_evaluation_*` custom stream frames:
 *   active    — set, nothing graded yet (or the last turn wasn't graded)
 *   grading   — a grader pass is running on the turn that just finished
 *   revising  — the grader sent the agent back; it is having another go
 *   met       — every criterion passed; the pill clears itself shortly after
 *   stalled   — the grader gave up (iteration cap, malformed rubric, grader error)
 */
interface Goal {
  text: string;
  status: "active" | "grading" | "revising" | "met" | "stalled";
  /** The grader's one-line reason, shown under a met/stalled pill. */
  note?: string;
}

/** How long a met goal stays visible before it clears itself. */
const GOAL_MET_LINGER_MS = 6000;

/** RubricMiddleware's `custom` stream payload (the fields we use). */
interface RubricFrame {
  type?: string;
  result?: string;
  explanation?: string;
}

/**
 * Map a grader verdict to the pill's status.
 *
 * Only `satisfied` is success. `needs_revision` means another agent pass is coming,
 * and the three remaining values are all "grading stopped without a pass" —
 * lumping them into one `stalled` state on purpose: the difference between an
 * iteration cap and a grader error matters to us, not to someone watching a demo,
 * and the explanation is shown either way.
 */
function statusFromVerdict(result: string | undefined): Goal["status"] {
  if (result === "satisfied") return "met";
  if (result === "needs_revision") return "revising";
  return "stalled";
}


/**
 * The answer bubble's text before any token arrives. Rendered as the animated
 * ReasoningText shimmer rather than shown literally, so the renderer, the creation site
 * and the teardown all have to agree on it: three copies of the string is how a stopped
 * run kept shimmering.
 */
/**
 * One readable line from a server error payload.
 *
 * First line only, and capped: a provider error can arrive with a traceback attached,
 * and a wall of Python in the chat bubble buries the sentence worth reading.
 */
function errorText(raw: string | undefined): string {
  const first = (raw || "").split("\n")[0].trim();
  return first.length > 300 ? first.slice(0, 299) + "…" : first;
}

const PLACEHOLDER_TEXT = "Working…";

/**
 * Word-by-word fade-in for streamed answers.
 *
 * Module scope, not an inline literal: Streamdown memoizes on `animated === prev`, so a
 * fresh object every render defeats that comparison on every chunk of the stream.
 *
 * Word-level rather than per character - per character on a long answer is a great deal
 * of simultaneous animation for no extra legibility.
 */
const ANSWER_ANIMATION = { animation: "fadeIn", sep: "word", duration: 260 } as const;

export default function ChatPanel({
  handleRef,
  voiceControl,
  assistantId,
  presets = [],
  getRunContext,
  onWidget,
  onArtifact,
  guard,
  resetKey,
  logo,
  industry,
  sandboxTarget,
  hasAssistant,
  assistantsLoaded = true,
  onOpenSettings,
  onActivity,
}: ChatPanelProps) {
  const [items, setItems] = useState<Item[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  /**
   * The active `/goal`, shown as a pill above the composer and sent with every turn
   * as deepagents' `rubric` state key. `status` is the last grader verdict, so the
   * pill can show that a turn is being graded and then that the goal was met — at
   * which point it clears itself (see `GOAL_MET_LINGER_MS`).
   */
  const [goal, setGoal] = useState<Goal | null>(null);
  const goalRef = useRef<Goal | null>(null);
  goalRef.current = goal;

  const idRef = useRef(0);
  const busyRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);
  const logRef = useRef<HTMLDivElement | null>(null);
  const firstRun = useRef(true);
  // Whether to keep the log pinned to the bottom as new content streams in.
  // Flips to false the moment the user scrolls up (so they can read history
  // mid-stream), and back to true when they return to the bottom or send.
  const stickToBottom = useRef(true);

  const nextId = () => `m${++idRef.current}`;

  // Reset conversation on resetKey change (assistant switch / new chat).
  useEffect(() => {
    if (firstRun.current) {
      firstRun.current = false;
      return;
    }
    abortRef.current?.abort();
    resetThread();
    busyRef.current = false;
    setBusy(false);
    setItems([]);
    // The goal lives on the thread's state, so a new thread has no goal.
    setGoal(null);
  }, [resetKey]);

  // Keep the log pinned to the newest message — but only while the user is at
  // the bottom. If they've scrolled up to read history mid-stream, leave their
  // position alone (stickToBottom is false) instead of yanking them back down.
  useEffect(() => {
    const el = logRef.current;
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight;
  }, [items]);

  // Track whether the user is at (or near) the bottom. A small threshold keeps
  // "stick" true through sub-pixel rounding and the last streamed line.
  const onLogScroll = () => {
    const el = logRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickToBottom.current = distanceFromBottom <= 60;
  };

  const patchItem = (id: string, fn: (it: Item) => Item) =>
    setItems((prev) => prev.map((it) => (it.id === id ? fn(it) : it)));

  /**
   * Mirror the LATEST question's chips + subagent groups out for the Graph tab. The
   * latest question, not the whole thread, so the graph shows the work being done now.
   * Derived from `items` so it stays in step with the chat rail by construction rather
   * than duplicating the streaming reducer.
   */
  useEffect(() => {
    if (!onActivity) return;
    let chips: ChipData[] = [];
    let subagents: SubagentGroup[] = [];
    for (const it of items) {
      // Only a new USER turn resets the picture. Resuming a human-review interrupt
      // appends a fresh (initially empty) activity item to the SAME question, so chips
      // have to ACCUMULATE across activity items: taking only the latest blanked the
      // graph the moment you hit Approve.
      if (it.kind === "user") {
        chips = [];
        subagents = [];
      } else if (it.kind === "activity") {
        chips = chips.length ? [...chips, ...it.chips] : it.chips;
      } else if (it.kind === "subagents") {
        subagents = subagents.length ? [...subagents, ...it.groups] : it.groups;
      }
    }
    onActivity({ chips, subagents, running: busy });
  }, [items, busy, onActivity]);

  /**
   * Run one turn: either a new question, or a resume of a run paused at a
   * human-review interrupt. Both share the whole streaming pipeline; a resume
   * simply carries `resume` instead of `messages` and does not clear the
   * dashboard (it is a continuation of the same turn).
   */
  const runTurn = async (opts: {
    question?: string;
    resume?: unknown;
    images?: ImageAttachment[];
    /**
     * Files uploaded to the VM alongside this turn. They travel differently from images:
     * an image rides IN the message, but a document is already on disk, so all the model
     * needs is to be told where. Without that it has no idea the file arrived - the
     * sandbox note only lists what setup seeded - which read as the agent ignoring an
     * attachment the composer had just confirmed.
     */
    docs?: { name: string; path: string }[];
    /** Distributed-tracing headers, so a voice-driven run nests under its tool span. */
    headers?: Record<string, string>;
    /**
     * Called with each tool the agent starts. Voice mode uses it to narrate progress
     * during a 60-second run; the typed path shows the same thing as chips.
     */
    onProgress?: (toolName: string) => void;
  }): Promise<TurnResult> => {
    const { question, resume, images = [], docs: sent = [], headers, onProgress } = opts;
    const isResume = resume !== undefined;
    // Returned rather than thrown: a programmatic caller (voice) needs something to say,
    // and "a turn was already running" is a normal race there, not a failure.
    if (busyRef.current) return { answer: "", widgets: [], error: "already running" };
    if (!isResume && !question) return { answer: "", widgets: [], error: "nothing to ask" };

    busyRef.current = true;
    setBusy(true);
    // A fresh turn always scrolls into view, even if the user had scrolled up.
    stickToBottom.current = true;

    const activityId = nextId();
    const subagentId = nextId();
    const bubbleId = nextId();
    setItems((prev) => [
      ...prev,
      ...(question
        ? [
            {
              kind: "user" as const,
              id: nextId(),
              text: question,
              images: images.map((img) => `data:${img.mime};base64,${img.data}`),
              docs: sent,
            },
          ]
        : []),
      { kind: "activity", id: activityId, chips: [] },
      { kind: "subagents", id: subagentId, groups: [] },
      { kind: "assistant", id: bubbleId, text: PLACEHOLDER_TEXT, streaming: true, markdown: false },
    ]);

    // Per-run mutable stream state (persists across the whole for-await loop).
    // These maps belong to the MAIN graph ONLY — non-empty-namespace (subagent)
    // frames are routed to `subState` below and must never touch these, or a
    // subagent's own model/tool output would leak into the answer/dashboard.
    const chipOrder: string[] = [];
    const chipMap: Record<string, ChipData> = {};
    const wOrder: string[] = [];
    const wLatest: Record<string, Widget> = {};
    const wFlushed = new Set<string>();
    // Artifact writes seen this run, keyed by tool_call id -> path. Lets the write's
    // ToolMessage (which carries only tool_call_id) mark the right artifact finished.
    const artifactPathByCall: Record<string, string> = {};
    // Message ids that turned out to carry tool calls. A streaming message TRANSITIONS:
    // its preamble text ("I'll look that up.") arrives several frames before its tool
    // calls do, so a per-frame "has no tool calls" test says yes, then no. Remembering
    // which ids ever had tools is what makes that judgement stick.
    const toolMsgIds = new Set<string>();
    // Message ids belonging to a middleware's own model call (the goal grader),
    // learned from metadata. Only needed on a server that does NOT suffix event
    // names with the namespace: there the frames look like main-graph output and
    // the checkpoint ns is the one place the middleware's name still shows.
    const middlewareMsgIds = new Set<string>();
    // langgraph_node per MAIN-graph message id, from `messages/metadata` events.
    // The main agent's messages are node "model"; a tool's internal LLM calls
    // (e.g. the synthetic data source) are node "tools" — we must NOT render
    // those as chat.
    const nodeById: Record<string, string> = {};
    // Effective namespace per message id, learned from either the SSE event-name
    // suffix or a `langgraph_checkpoint_ns` in messages/metadata (fallback for
    // servers that don't suffix the event name). [] means the root/main graph.
    const nsById: Record<string, string[]> = {};
    // Per-subagent reducer state, keyed by subagentIdentity(ns).key. Each bucket
    // tracks its own chips/nodes/text independently of main and of each other.
    const subOrder: string[] = [];
    const subState: Record<
      string,
      {
        label: string;
        chipOrder: string[];
        chipMap: Record<string, ChipData>;
        nodeById: Record<string, string>;
        text: string;
      }
    > = {};
    // Main-graph tool calls that dispatch subagents (`task`, and an `eval` whose
    // script calls `task()`), in the order the agent emitted them. That ORDER is
    // what ties a dispatch to the card it produced — see `dispatchFor`.
    const dispatchOrder: string[] = [];
    // The `task` TOOL's own args, keyed by its tool_call id. An interpreter
    // dispatch has no args in the stream at all; it is read back off the script.
    const taskArgs: Record<string, TaskDispatch> = {};
    let answer = "";
    /**
     * What the bubble is currently SHOWING, which is not the same as the answer.
     *
     * The agent narrates before it acts ("I have the intake details, now I'll draft the
     * letter"), and that narration is worth leaving on screen while the tools run. But
     * it is not the answer: `answer` is returned from this function and read aloud by
     * voice mode, and it gates the "Building your dashboard…" line below. Conflating the
     * two meant a preamble got spoken as the answer; withdrawing the preamble the moment
     * tool calls appeared meant it blinked out with nothing to replace it. Tracking both
     * separately is what allows "linger until something replaces it".
     */
    let shownText = "";
    let runId: string | null = null;
    let errorMsg: string | null = null;
    let interrupt: ReviewInterrupt | null = null;

    const syncChips = () =>
      patchItem(activityId, (it) =>
        it.kind === "activity" ? { ...it, chips: chipOrder.map((id) => ({ ...chipMap[id] })) } : it,
      );
    const setBubble = (patch: Partial<Omit<AssistantItem, "kind" | "id">>) =>
      patchItem(bubbleId, (it) => (it.kind === "assistant" ? { ...it, ...patch } : it));

    // Widgets this turn flushed. The dashboard accumulates across turns; a caller that
    // has to SAY something only wants what this turn added.
    const turnWidgets: Widget[] = [];

    const flushWidget = (id: string) => {
      const w = wLatest[id];
      if (w && !wFlushed.has(id) && widgetLooksComplete(w)) {
        wFlushed.add(id);
        turnWidgets.push(w);
        // Widgets ACCUMULATE across turns — the dashboard is a persistent canvas for
        // the whole conversation, cleared only by New Chat / assistant switch. So an
        // "add a chart" follow-up appends (both charts stay), matching what the agent
        // tells the user, and a text-only turn leaves the dashboard untouched.
        onWidget(w);
        // Only fills an EMPTY bubble: a narration already on screen says more than
        // this does, and overwriting it would be the same blink-out by another route
        // (push_widget is a tool, so it fires exactly when narration is showing).
        if (!shownText) setBubble({ text: "Building your dashboard…" });
      }
    };

    const onStreamMessage = (msg: ThreadMessage | undefined) => {
      if (!msg || typeof msg !== "object") return;
      const node = msg.id ? nodeById[msg.id] : undefined;
      if (msg.type === "ai") {
        const tcs = msg.tool_calls || [];
        for (const tc of tcs) {
          const name = tc.name || "";
          const args = tc.args || {};
          if (name === "push_widget") {
            // The real tool_call id, never a fallback. `toolCallKey` falls back to
            // `<msgId>:<name>`, and that fallback is what mangled dashboards: the early
            // arg frames of a tool_use block can arrive before the id does, so the
            // half-streamed widget was filed under the fallback key, the real id then
            // opened a SECOND entry, and the fallback entry was no longer last in
            // wOrder - so the flush loop below pushed a one-cell table and a one-letter
            // "Key findings" onto the canvas, where wFlushed made it permanent.
            //
            // A fallback also collides when one message pushes several widgets, since
            // every one of them keys to the same `<msgId>:push_widget`.
            //
            // The chip branch already skips id-less frames for the same reason.
            const id = tc.id;
            if (!id) continue;
            wLatest[id] = widgetFromArgs(args);
            if (!wOrder.includes(id)) {
              wOrder.push(id);
              // Progress fires HERE too, not just on the chip path below. A widget IS
              // this tool's output, so it never becomes a chip - which also meant the
              // voice status line, which rides on chip creation, went silent for the
              // one tool whose work takes longest and is most worth narrating. Guarded
              // by first-sight like the chip branch: args stream in over many frames.
              onProgress?.(name);
            }
            // Flush every widget except the one still streaming (last in order).
            for (let i = 0; i < wOrder.length - 1; i++) flushWidget(wOrder[i]);
          } else {
            // An HTML artifact write is ALSO a normal chip, so this runs alongside the
            // chip path rather than replacing it, and deliberately before the id guard
            // below: the earliest arg frames can arrive without a tool_call id, and
            // those carry the opening tags we want on screen soonest.
            if (name === "delete") {
              const a = args as { file_path?: string };
              // On the CALL, not the result: the tab is showing a file the agent has
              // decided to remove either way, and a delete that fails leaves a tab the
              // next write recreates.
              if (isHtmlArtifactPath(a.file_path)) {
                onArtifact?.({
                  path: a.file_path as string,
                  content: "",
                  streaming: false,
                  deleted: true,
                });
              }
            }
            if (name === "write_file" || name === "edit_file") {
              const a = args as { file_path?: string; content?: string };
              if (isHtmlArtifactPath(a.file_path)) {
                const path = a.file_path as string;
                if (tc.id) artifactPathByCall[tc.id] = path;
                // edit_file carries old_string/new_string, not the document, so it has
                // no content to stream. It still registers the artifact (creating its
                // tab) and the post-write re-read supplies the edited text.
                onArtifact?.({
                  path,
                  content: typeof a.content === "string" ? a.content : "",
                  streaming: true,
                });
              }
            }
            // A chip MUST be keyed by the real tool_call id so the tool's result
            // (ToolMessage.tool_call_id) can match and clear its spinner. Skip
            // partial stream frames that don't carry the id yet — keying a chip on a
            // fallback would create a phantom that never receives a result and spins
            // forever (an ever-climbing timer), duplicating the real chip.
            const id = tc.id;
            if (!id) continue;
            // Remember what each dispatch asked for, so its subagent card can name
            // the specialist and show the instruction it was invoked with.
            if (name === "task" || name === "eval") {
              if (!dispatchOrder.includes(id)) dispatchOrder.push(id);
            }
            if (name === "task") {
              const a = args as { description?: string; subagent_type?: string };
              taskArgs[id] = {
                subagentType: a.subagent_type || "",
                description: a.description || "",
              };
            }
            const summary = chipArgSummary(name, args);
            // Carry the source/command a code-running tool executed (eval's `code`,
            // execute's `command`) so the expanded chip can show it highlighted.
            // Accumulates across partial frames just like the summary.
            const ci = chipCode(name, args);
            if (!chipMap[id]) {
              chipMap[id] = { id, name, arg: summary, result: null, code: ci?.code, codeLang: ci?.lang };
              chipOrder.push(id);
              // Real progress, from the agent's own tool calls (the shell throttles it).
              onProgress?.(name);
            } else {
              chipMap[id] = {
                ...chipMap[id],
                arg: summary,
                ...(ci && { code: ci.code, codeLang: ci.lang }),
              };
            }
            syncChips();
          }
        }
        // Final answer = a MAIN-agent AI message with text that never carried tools.
        // Exclude tool-internal LLM output (node "tools") — it must not leak into chat.
        //
        // Preamble is DISCARDED, not shown. It used to reach the bubble during the few
        // frames before the tool calls appeared and then stay there, unchallenged, for
        // the whole tool phase - reading as an answer the agent had not given. It also
        // left `answer` non-empty, which silently suppressed the "Building your
        // dashboard…" line below and, since runTurn returns `answer`, was what voice
        // mode read aloud when a run ended early.
        if (tcs.length > 0 && msg.id) toolMsgIds.add(msg.id);
        const text = contentToText(msg.content);
        const isPreamble = !!msg.id && toolMsgIds.has(msg.id);
        if (text && node !== "tools") {
          // Both kinds of text go on screen and STAY there until something replaces
          // them: the next narration, or the answer. Only the answer becomes `answer`.
          shownText = text; // partial content is cumulative per message id
          if (!isPreamble) answer = text;
          setBubble({ text, streaming: true, markdown: false });
        }
      } else if (msg.type === "tool" && msg.name !== "push_widget") {
        const cid = msg.tool_call_id;
        // The write landed: hand the artifact over as finished so App re-reads the
        // file. Until this fires, what the tab shows is the streamed argument, which
        // for edit_file is not the document at all.
        if (cid && artifactPathByCall[cid]) {
          onArtifact?.({ path: artifactPathByCall[cid], content: "", streaming: false });
        }
        if (cid && chipMap[cid]) {
          chipMap[cid] = { ...chipMap[cid], result: contentToText(msg.content) };
          syncChips();
        }
      }
    };

    /* ---- Subagent (non-empty namespace) pipeline — fully separate state ---- */

    const ensureSub = (ns: string[]) => {
      const { key, label } = subagentIdentity(ns);
      if (!subState[key]) {
        subState[key] = { label, chipOrder: [], chipMap: {}, nodeById: {}, text: "" };
        subOrder.push(key);
      }
      return key;
    };

    /**
     * Who this subagent is and what it was told to do.
     *
     * Matched BY ORDER, which needs saying: a subagent's namespace is
     * `tools:<uuid>` — a fresh subgraph id, NOT the id of the tool call that
     * dispatched it (verified against a live run; the previous code assumed the
     * call id and so never matched, which is why every card just read "Subagent").
     * Nothing in the stream links the two, so the Nth dispatch the agent emitted is
     * paired with the Nth subagent root that appeared.
     *
     * A dispatch source is either the `task` tool (one subagent, args in the call)
     * or an `eval` whose script calls `task()` (read back off the script; a fan-out
     * shares ONE root and separates by branch index). Sources that dispatch nothing
     * are skipped so they can't shift the pairing.
     */
    const dispatchFor = (key: string): TaskDispatch | undefined => {
      const sources = dispatchOrder
        .map((id) => (taskArgs[id] ? [taskArgs[id]] : parseTaskDispatches(chipMap[id]?.code || "")))
        .filter((list) => list.length);
      const roots = [...new Set(subOrder.map(subagentRoot))];
      const list = sources[roots.indexOf(subagentRoot(key))];
      if (!list) return undefined;
      return list[taskBranch(key)] || list[0];
    };

    // Rebuild the SubagentItem from subState. `done` stays false while streaming;
    // the finally block freezes it (and any pending chip) once the run ends.
    const syncSubagents = () =>
      patchItem(subagentId, (it) =>
        it.kind === "subagents"
          ? {
              ...it,
              groups: subOrder.map((k) => {
                const dispatch = dispatchFor(k);
                const type = dispatch?.subagentType || "";
                return {
                  key: k,
                  // The specialist's own name beats the generic "Subagent" — which
                  // of the fleet ran is the point of showing the card at all.
                  label: type ? type[0].toUpperCase() + type.slice(1) : subState[k].label,
                  type: type || undefined,
                  chips: subState[k].chipOrder.map((id) => ({ ...subState[k].chipMap[id] })),
                  text: subState[k].text,
                  invokedWith: dispatch?.description || undefined,
                  done: false,
                };
              }),
            }
          : it,
      );

    const onSubagentMessage = (ns: string[], msg: ThreadMessage | undefined) => {
      if (!msg || typeof msg !== "object") return;
      const key = ensureSub(ns);
      const st = subState[key];
      const node = msg.id ? st.nodeById[msg.id] : undefined;
      if (msg.type === "ai") {
        const tcs = msg.tool_calls || [];
        for (const tc of tcs) {
          // Same keying rule as main chips: a chip MUST carry the real tool_call
          // id so the matching ToolMessage can clear its spinner.
          const id = tc.id;
          if (!id) continue;
          const name = tc.name || "";
          const summary = chipArgSummary(name, tc.args || {});
          if (!st.chipMap[id]) {
            st.chipMap[id] = { id, name, arg: summary, result: null };
            st.chipOrder.push(id);
          } else {
            st.chipMap[id] = { ...st.chipMap[id], arg: summary };
          }
        }
        const text = contentToText(msg.content);
        if (text && tcs.length === 0 && node !== "tools") st.text = text;
        syncSubagents();
      } else if (msg.type === "tool") {
        const cid = msg.tool_call_id;
        if (cid && st.chipMap[cid]) {
          st.chipMap[cid] = { ...st.chipMap[cid], result: contentToText(msg.content) };
          syncSubagents();
        }
      }
    };

    const controller = new AbortController();
    abortRef.current = controller;

    const runContext = getRunContext();
    try {
      const tid = await ensureThread();
      for await (const { event, data, namespace } of runStream({
        threadId: tid,
        assistantId,
        ...(isResume
          ? { resume }
          : { messages: [{ role: "user", content: imageContent(withDocs(question!, sent), images) }] }),
        context: runContext,
        signal: controller.signal,
        headers,
        // Sticky: re-sent every turn until the goal is met or cleared. A resume
        // carries no input, but the rubric is already on the thread's state.
        rubric: goalRef.current?.text,
      })) {
        let parsed: unknown;
        try {
          parsed = JSON.parse(data);
        } catch {
          continue;
        }
        if (event === "metadata") {
          // run_id ONLY from a root (non-subagent) frame — a subagent frame must
          // not hijack the feedback run_id.
          if (!isSubagentNamespace(namespace)) {
            const d = parsed as { run_id?: string };
            if (d && d.run_id) runId = d.run_id;
          }
          continue;
        }
        if (event === "custom") {
          // RubricMiddleware grading the turn against the active goal. Root frames
          // only: a subagent cannot finish the user's goal.
          const frame = parsed as RubricFrame;
          if (!isSubagentNamespace(namespace) && frame?.type?.startsWith("rubric_evaluation")) {
            if (frame.type === "rubric_evaluation_start") {
              setGoal((g) => (g ? { ...g, status: "grading" } : g));
            } else {
              const status = statusFromVerdict(frame.result);
              setGoal((g) => (g ? { ...g, status, note: frame.explanation || "" } : g));
            }
          }
          continue;
        }
        if (event === "error") {
          // Only surface root-graph errors in the main bubble; a subagent error
          // must not leak into the main answer.
          if (!isSubagentNamespace(namespace)) {
            const d = parsed as { error?: string; message?: string };
            // `message` FIRST. The server sends `error` as the exception class and
            // `message` as the detail, and preferring the class put
            // "AnthropicInvalidRequestError" on screen while discarding "Your credit
            // balance is too low to access the Anthropic API" - the only part anyone
            // can act on. That cost a trip through the traces to learn something the
            // UI had already been handed.
            errorMsg = errorText(d?.message) || errorText(d?.error) || "run error";
          }
          continue;
        }
        // A middleware's own model call (the goal grader) streams on this channel
        // too. Its frames are AI messages with no tool calls, i.e. shaped exactly
        // like a final answer, so they must be dropped before any routing: the
        // verdict JSON was landing in the chat as the assistant's reply. The
        // grader's `custom` frames above are how its result reaches the UI.
        if (isMiddlewareNamespace(namespace)) continue;
        if (event === "messages/metadata") {
          // { "<message_id>": { metadata: { langgraph_node, langgraph_checkpoint_ns, ... } } }
          const d = parsed as Record<
            string,
            {
              metadata?: {
                langgraph_node?: string;
                langgraph_checkpoint_ns?: string;
                checkpoint_ns?: string;
              };
            }
          >;
          if (d && typeof d === "object") {
            for (const [mid, info] of Object.entries(d)) {
              const meta = info?.metadata;
              const n = meta?.langgraph_node;
              if (isMiddlewareNamespace(parseCheckpointNs(meta?.langgraph_checkpoint_ns))) {
                middlewareMsgIds.add(mid);
                continue;
              }
              // Route by the EVENT-NAME namespace ONLY. Real subagents are streamed
              // subgraphs, so their frames carry a `tools:<call_id>` event-name
              // suffix. The metadata `checkpoint_ns` is NOT a reliable subagent
              // signal: the MAIN agent's own `tools` node also has a `tools:<uuid>`
              // checkpoint_ns, so using it as a fallback here tagged every main-agent
              // tool RESULT as a subagent — the tool chip never got its result and
              // couldn't be expanded (datasearch/read_file/execute).
              if (isSubagentNamespace(namespace)) {
                nsById[mid] = namespace;
                if (n) subState[ensureSub(namespace)].nodeById[mid] = n;
              } else if (n) {
                nodeById[mid] = n;
              }
            }
          }
          continue;
        }
        if (event === "updates") {
          if (isSubagentNamespace(namespace)) {
            // eval/`task()`-from-code subagents don't emit a namespaced token
            // stream — they surface as namespaced STATE updates. Pull each node's
            // messages into that subagent's card (its model output = its result,
            // its tool calls = its chips), keyed by the full namespace so parallel
            // dispatches land in separate cards.
            const nodes = parsed as Record<string, { messages?: ThreadMessage[] } | null>;
            if (nodes && typeof nodes === "object") {
              const key = ensureSub(namespace);
              for (const [node, upd] of Object.entries(nodes)) {
                for (const m of upd?.messages || []) {
                  if (m && typeof m === "object" && m.id) subState[key].nodeById[m.id] = node;
                  onSubagentMessage(namespace, m);
                }
              }
            }
            continue;
          }
          // A tool called interrupt() — the run is now paused awaiting a human.
          // HITL review is a main-graph concern (main namespace only).
          const d = parsed as { __interrupt__?: Array<{ value?: ReviewInterrupt }> };
          const value = d?.__interrupt__?.[0]?.value;
          if (value && typeof value === "object") interrupt = value;
          continue;
        }
        if (event === "messages/partial" || event === "messages/complete") {
          const msg = (Array.isArray(parsed) ? parsed[0] : parsed) as ThreadMessage;
          if (msg?.id && middlewareMsgIds.has(msg.id)) continue;
          // Partition strictly by namespace: ONLY root frames feed the main
          // pipeline; everything else is a subagent (kept out of answer/widgets).
          const ns = effectiveNamespace(namespace, msg?.id, nsById);
          // Route to a subagent card ONLY for a real `tools:` subagent namespace;
          // main-agent frames (empty OR internal non-tools ns) feed the answer.
          if (isSubagentNamespace(ns)) onSubagentMessage(ns, msg);
          else onStreamMessage(msg);
        }
      }
      // Flush the last (still-open) widget now the stream has ended.
      wOrder.forEach(flushWidget);

      if (interrupt) {
        // Paused, not finished: hand over to the review editor. Any preamble the
        // agent streamed stays (minus the cursor); the bare "Working…" placeholder
        // is dropped since the review card now explains the state. No feedback row
        // either — there is no answer to rate yet.
        const pending = interrupt;
        // Narration counts as having spoken: the comment below is about keeping it.
        const spoke = !!shownText;
        setBubble({ streaming: false, markdown: false });
        setItems((prev) => [
          ...prev.filter((it) => spoke || it.id !== bubbleId),
          { kind: "review", id: nextId(), review: pending, done: false },
        ]);
        // A caller driving this by voice cannot see the review card, so hand back a
        // line it can read out. The card is still rendered for whoever is looking.
        return {
          answer,
          widgets: turnWidgets,
          approval: describeInterrupt(pending),
          runId: runId || undefined,
        };
      }

      if (answer) setBubble({ streaming: false, markdown: true, text: answer });
      else if (errorMsg) setBubble({ streaming: false, markdown: false, text: "⚠️ " + errorMsg });
      else if (wFlushed.size)
        setBubble({ streaming: false, markdown: false, text: "Dashboard ready." });
      else setBubble({ streaming: false, markdown: false, text: "(no response)" });

      if (runId)
        setItems((prev) => [
          ...prev,
          { kind: "feedback", id: nextId(), runId: runId!, workspace: runContext.ls_workspace },
        ]);
      return { answer, widgets: turnWidgets, error: errorMsg || undefined, runId: runId || undefined };
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        setBubble({
          streaming: false,
          markdown: false,
          text: "⚠️ Request failed: " + (e as Error).message,
        });
      }
      return { answer: "", widgets: turnWidgets, error: (e as Error).message };
    } finally {
      busyRef.current = false;
      setBusy(false);
      // Stop the answer bubble's shimmer. A bubble still marked streaming HERE can only
      // be an aborted run: the success path has already written the final answer and the
      // catch above has written the error, and both clear the flag. Abort is the one exit
      // that writes nothing, deliberately (an "⚠️ Request failed: AbortError" for pressing
      // Stop would be nonsense), which left the placeholder shimmering forever.
      patchItem(bubbleId, (it) =>
        it.kind === "assistant" && it.streaming
          ? {
              ...it,
              streaming: false,
              // Keep whatever tokens arrived before the stop; only the bare placeholder
              // gets replaced, since "Working…" frozen in place reads as a hang.
              text: !it.text || it.text === PLACEHOLDER_TEXT ? "Stopped." : it.text,
            }
          : it,
      );
      // The run is over: freeze any chip still without a result so its spinner +
      // elapsed timer stop (a tool whose result never streamed shouldn't count
      // forever). No syncChips runs after this, so patching the item is safe.
      patchItem(activityId, (it) =>
        it.kind === "activity"
          ? { ...it, chips: it.chips.map((c) => (c.result === null ? { ...c, stopped: true } : c)) }
          : it,
      );
      // Same freeze for each subagent: mark done (stops the card spinner) and
      // stop any chip whose result never streamed.
      patchItem(subagentId, (it) =>
        it.kind === "subagents"
          ? {
              ...it,
              groups: it.groups.map((g) => ({
                ...g,
                done: true,
                chips: g.chips.map((c) => (c.result === null ? { ...c, stopped: true } : c)),
              })),
            }
          : it,
      );
    }
  };

  /**
   * Images attached to the NEXT turn. Held here rather than in the composer because
   * the composer is a vendored beUI component and a turn's images have to travel with
   * its text into `runTurn`.
   */
  const [attached, setAttached] = useState<ImageAttachment[]>([]);
  const [attachError, setAttachError] = useState("");
  /**
   * Documents dropped on the chat. Unlike images these do NOT ride in the turn — the
   * model cannot read a PDF from a message — they go into the VM, where `execute` and
   * pypdf can open them. Kept as chips so the presenter can see where they landed.
   */
  const [docs, setDocs] = useState<{ name: string; path: string }[]>([]);
  const [dropping, setDropping] = useState(false);
  const [uploading, setUploading] = useState(false);
  const imagePicker = useRef<HTMLInputElement | null>(null);

  /** Accept images from the picker, a drop, or a paste. Rejections are per-file. */
  const attach = async (files: Iterable<File> | null) => {
    const list = Array.from(files ?? []);
    if (!list.length) return;
    setAttachError("");
    for (const file of list) {
      const { image, error } = await readImageAttachment(file);
      if (error) setAttachError(error);
      else if (image) setAttached((prev) => [...prev, image]);
    }
  };

  /**
   * A drop on the chat: images become part of the turn (the model sees them), and
   * everything else goes to /workspace/data (the model opens it with code). Routing by
   * type rather than asking, because dragging a PDF onto a conversation has exactly one
   * sensible meaning.
   */
  const dropFiles = async (dropped: FileList | null) => {
    const files = Array.from(dropped ?? []);
    if (!files.length) return;
    const images = files.filter((f) => IMAGE_MIME_TYPES.includes(f.type));
    const documents = files.filter((f) => !IMAGE_MIME_TYPES.includes(f.type));
    if (images.length) await attach(images);
    if (!documents.length) return;
    if (!sandboxTarget?.agent_repo && !sandboxTarget?.customer) {
      setAttachError("Choose an assistant before sending it a document.");
      return;
    }
    setUploading(true);
    try {
      const result = await uploadSandboxFiles(sandboxTarget, documents);
      if (result.written.length) {
        setDocs((prev) => [...prev, ...result.written]);
        setAttachError("");
      }
      if (result.failed.length) {
        setAttachError(result.failed.map((f) => `${f.name}: ${f.error}`).join("; "));
      }
    } catch (e) {
      setAttachError(e instanceof Error ? e.message : String(e));
    } finally {
      setUploading(false);
    }
  };

  // A met goal clears itself: the work it described is done, and a pill that stays
  // up after that reads as "still going". Lingers first so the tick is seen.
  useEffect(() => {
    if (goal?.status !== "met") return;
    const t = setTimeout(() => setGoal(null), GOAL_MET_LINGER_MS);
    return () => clearTimeout(t);
  }, [goal?.status]);

  /**
   * The question, plus a line naming any files this turn uploaded.
   *
   * Appended to the user's text rather than sent as context, because the model has to
   * see it as part of what was asked: "does this receipt qualify" only makes sense next
   * to the path of the receipt. The user's own bubble shows the files as chips instead,
   * so this plumbing never appears in the transcript.
   */
  const withDocs = (question: string, sent: { name: string; path: string }[]) => {
    if (!sent.length) return question;
    const list = sent.map((d) => d.path).join(", ");
    const files = sent.length === 1 ? "file" : "files";
    return `${question}\n\n[The user just uploaded ${sent.length} ${files} to your filesystem: ${list}. Open and use it to answer.]`;
  };

  /** A line the user typed that the agent never sees (a `/goal` ack). */
  const note = (text: string) =>
    setItems((prev) => [
      ...prev,
      { kind: "assistant", id: nextId(), text, streaming: false, markdown: false },
    ]);

  /**
   * `/goal …` — set, show, or clear the objective the agent is graded against.
   *
   * Returns the text to run as this turn, or null when the command consumed the
   * input. SETTING a goal also runs it: "/goal build me a dashboard" means both
   * "here is what done looks like" and "off you go" — parking the objective and
   * waiting to be asked again is not what anyone types it expecting.
   */
  const handleGoalCommand = (cmd: GoalCommand): string | null => {
    const current = goalRef.current;
    if (cmd.kind === "show") {
      note(
        current
          ? `Current goal: ${current.text}`
          : "No goal set. Try `/goal <what done looks like>`.",
      );
      return null;
    }
    if (cmd.kind === "clear") {
      setGoal(null);
      goalRef.current = null;
      note(current ? "Goal cleared." : "No goal to clear.");
      return null;
    }
    const next: Goal = { text: cmd.text, status: "active" };
    setGoal(next);
    // The ref, not just the state: this turn reads it for the run's rubric before
    // React has re-rendered, so the goal would otherwise miss its own first turn.
    goalRef.current = next;
    return cmd.text;
  };

  /** New question from the composer or a quick action. */
  const send = (raw: string) => {
    let question = (raw || "").trim();
    if (!question || busyRef.current) return;
    const cmd = parseGoalCommand(question);
    if (cmd) {
      const objective = handleGoalCommand(cmd);
      if (!objective) return;
      question = objective;
    }
    // App-owned guard: a returned string blocks the send (and App opens settings).
    const blocked = guard?.(question);
    if (blocked) {
      setItems((prev) => [
        ...prev,
        { kind: "assistant", id: nextId(), text: blocked, streaming: false, markdown: false },
      ]);
      return;
    }
    const images = attached;
    setAttached([]);
    // Documents stay in the VM, but the chip is about the turn being sent — leaving it
    // up would stack one per drop across a whole demo. They travel INTO the turn, which
    // is what tells the agent they exist and puts them in the transcript; the Files
    // panel remains the durable record.
    const sent = docs;
    setDocs([]);
    setAttachError("");
    void runTurn({ question, images, docs: sent });
  };

  /**
   * The voice shell's way in. Deliberately the SAME `runTurn` the composer calls: a
   * spoken question has to produce the same widgets, chips, transcript and trace as a
   * typed one, and a second code path would drift from the first within a week.
   *
   * The App guard still applies (no assistant selected, etc.) so voice cannot start a
   * run the typed path would have refused.
   */
  useImperativeHandle(
    handleRef,
    () => ({
      ask: async (
        question: string,
        headers?: Record<string, string>,
        onProgress?: (toolName: string) => void,
      ) => {
        const blocked = guard?.(question);
        if (blocked) return { answer: blocked, widgets: [] };
        return runTurn({ question, headers, onProgress });
      },
      resumeWith: (value: unknown) => runTurn({ resume: value }),
      busy: () => busyRef.current,
    }),
    // `runTurn` and `guard` are re-created every render; the handle reads them through
    // the closure it is rebuilt with, so no dependency list is needed beyond the ref.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [handleRef],
  );

  /** Human approved a paused artifact — resume the run with their version. */
  const approveReview = (itemId: string, value: Record<string, unknown>) => {
    setItems((prev) =>
      prev.map((it) => (it.id === itemId && it.kind === "review" ? { ...it, done: true } : it)),
    );
    void runTurn({ resume: value });
  };

  // Submit from beUI's PromptInput: clear the composer, then run the turn.
  // (send() re-reads the passed text and applies the App guard.)
  const submit = (text: string) => {
    setInput("");
    send(text);
  };

  const heroPlaceholder = industry
    ? `Ask me anything about ${industry}…`
    : "Ask a question…";

  // The composer is beUI's PromptInput (auto-growing textarea, built-in send/stop
  // buttons, Enter-to-send / Shift+Enter newline). The hero variant (shown only
  // before the first prompt) keeps a resting brand glow via a wrapper; the bottom
  // variant sits on a top border. `loading` swaps the send button for a stop that
  // aborts the in-flight stream.
  /**
   * The attach control lives in PromptInput's `leadingAction`, i.e. INSIDE its bordered
   * form on the same row as send. It first sat in a row underneath, which read as
   * bolted onto the card rather than part of it.
   */
  const attachButton = (
    <>
      <input
        ref={imagePicker}
        type="file"
        /**
         * NO `accept` filter, deliberately. It used to be the image MIME types, which
         * greyed every PDF out in the file picker while dragging that same PDF onto the
         * chat worked fine - the two ways of attaching a file disagreed about which
         * files exist. `dropFiles` routes by type either way, and the upload route
         * restricts by name/count/size rather than type, so there is nothing here for a
         * filter to usefully enforce.
         */
        multiple
        className="hidden"
        onChange={(e) => {
          // Same routing as a drop: images join the turn, documents go to the VM.
          void dropFiles(e.target.files);
          e.target.value = "";
        }}
      />
      <Button
        type="button"
        variant="ghost"
        size="icon"
        aria-label="Attach a file"
        title="Attach an image for this message, or a document to send to the agent's files"
        className="size-8 rounded-full"
        disabled={busy}
        onClick={() => imagePicker.current?.click()}
      >
        {uploading ? (
          <IconLoader2 size={16} className="animate-spin" />
        ) : (
          <IconPaperclip size={16} />
        )}
      </Button>
    </>
  );

  // Slash-command affordances, derived from what is currently typed:
  //   suggestions — the palette, while the first word is still an unfinished command
  //   active      — the command is complete, so it shows as a token on the composer
  const firstWord = input.split(/\s/)[0];
  const active = input.startsWith("/")
    ? COMMANDS.find((c) => new RegExp(`^${c.name}\\b`, "i").test(input))
    : undefined;
  const suggestions =
    input.startsWith("/") && !active
      ? COMMANDS.filter((c) => c.name.startsWith(firstWord.toLowerCase()))
      : [];

  /** Complete the composer to a command, ready for its argument. */
  const completeCommand = (name: string) => setInput(name + " ");

  const composer = (variant: "hero" | "bottom") => (
    <div
      className={
        variant === "hero"
          ? "w-full rounded-2xl shadow-[0_0_16px_-5px_color-mix(in_oklch,var(--brand-primary)_38%,transparent)]"
          : "border-t border-border px-3.5 py-3"
      }
      // Drop anywhere on the composer. Images join the turn; documents go to the VM.
      onDragOver={(e) => {
        e.preventDefault();
        setDropping(true);
      }}
      onDragLeave={() => setDropping(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDropping(false);
        void dropFiles(e.dataTransfer?.files ?? null);
      }}
    >
      {/* Typing "/" offers the commands; once one is complete it becomes a token, so
          a mistyped command is visibly NOT one before it is sent as a question. */}
      {suggestions.length > 0 && (
        <div className="mb-1.5 flex flex-col overflow-hidden rounded-lg border border-border bg-panel-2">
          {suggestions.map((c) => (
            <button
              key={c.name}
              type="button"
              className="flex items-baseline gap-2 px-2.5 py-1.5 text-left text-[12px] hover:bg-brand/10"
              onClick={() => completeCommand(c.name)}
            >
              <span className="font-mono font-semibold text-brand">{c.name}</span>
              <span className="text-[11px] text-muted-foreground">{c.hint}</span>
            </button>
          ))}
        </div>
      )}
      {active && (
        <div className="mb-1.5">
          <span className="inline-flex items-center gap-1 rounded-md border border-brand/50 bg-brand/10 px-1.5 py-0.5 font-mono text-[11px] font-semibold text-brand">
            <IconTarget size={12} />
            {active.name}
          </span>
          <span className="ml-1.5 text-[11px] text-muted-foreground">{active.hint}</span>
        </div>
      )}
      {goal && <GoalPill goal={goal} onClear={() => setGoal(null)} />}
      {/* What is riding with the next turn: images the model will see, documents now
          sitting in its VM. Above the box because they are content, not controls. */}
      {attached.length || docs.length || attachError ? (
        <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
          {attached.map((img, i) => (
            <span
              key={`${img.name}-${i}`}
              className="inline-flex items-center gap-1 rounded-md border border-border bg-panel-2 px-1.5 py-0.5 text-[11px]"
            >
              <img
                src={`data:${img.mime};base64,${img.data}`}
                alt=""
                className="h-5 w-5 rounded object-cover"
              />
              <span className="max-w-[12rem] truncate">{img.name}</span>
              <button
                type="button"
                aria-label={`Remove ${img.name}`}
                className="text-muted-foreground hover:text-foreground"
                onClick={() => setAttached((prev) => prev.filter((_, j) => j !== i))}
              >
                ×
              </button>
            </span>
          ))}
          {docs.map((doc, i) => (
            <span
              key={`${doc.path}-${i}`}
              className="inline-flex items-center gap-1 rounded-md border border-border bg-panel-2 px-1.5 py-0.5 text-[11px] text-muted-foreground"
              title={doc.path}
            >
              <IconFileText size={12} />
              <span className="max-w-[12rem] truncate text-foreground">{doc.name}</span>
              <span className="hidden sm:inline">in the agent&apos;s files</span>
            </span>
          ))}
          {attachError ? <span className="text-[11px] text-destructive">{attachError}</span> : null}
        </div>
      ) : null}
      <PromptInput
        value={input}
        onValueChange={setInput}
        onSubmit={(text) => submit(text)}
        loading={busy}
        onStop={() => abortRef.current?.abort()}
        placeholder={variant === "hero" ? heroPlaceholder : "Ask a question…"}
        minRows={variant === "hero" ? 2 : 1}
        aria-label="Prompt"
        // Tags the underlying textarea (PromptInput spreads unknown props onto it) so a
        // quick-action click can put the caret in it. The component is vendored and
        // exposes no ref, and only one composer is mounted at a time.
        data-chat-composer=""
        leadingAction={attachButton}
        trailingAction={voiceControl}
        onKeyDown={(e) => {
          // PromptInput calls this BEFORE its own Enter-to-send and honours
          // defaultPrevented, so completing here beats sending a half-typed command.
          if (suggestions.length === 1 && (e.key === "Tab" || e.key === "Enter")) {
            e.preventDefault();
            completeCommand(suggestions[0].name);
          }
        }}
        className={dropping ? "border-[var(--brand-primary)]" : undefined}
        // Paste is how a screenshot actually arrives; the button is the fallback.
        onPaste={(e) => {
          const files = Array.from(e.clipboardData?.files ?? []);
          if (files.length) {
            e.preventDefault();
            void dropFiles(e.clipboardData?.files ?? null);
          }
        }}
      />
    </div>
  );

  return (
    <div className="flex min-h-0 w-full flex-1 flex-col bg-panel">
      <div
        ref={logRef}
        onScroll={onLogScroll}
        className="flex min-h-0 flex-1 flex-col gap-3 overflow-x-hidden overflow-y-auto p-[18px]"
      >
        {items.length === 0 ? (
          hasAssistant ? (
            /* Hero: logo + prompt + centered composer + quick prompts */
            <div className="flex flex-1 flex-col items-center justify-center gap-5 px-4">
              <LogoGlyph logo={logo} size={64} />
              <h2 className="font-heading text-2xl font-bold tracking-tight">What can I help with?</h2>
              <div className="w-full max-w-xl">{composer("hero")}</div>
              {presets.length > 0 && (
                <div className="flex w-full max-w-xl flex-col gap-1.5">
                  {presets.map((p, i) => (
                    <PresetButton
                      key={i}
                      action={p}
                      disabled={busy}
                      // Loads the composer instead of sending. A preset is a long,
                      // specific question and the presenter usually wants to adjust it
                      // (a different store, a different claim number) before it goes;
                      // sending on click meant backing out of a run to change one word.
                      onClick={() => {
                        setInput(p.question);
                        // Focus so the caret is where the eye already is, at the end of
                        // the text that just appeared, ready to edit or hit send.
                        requestAnimationFrame(() => {
                          const box = document.querySelector<HTMLTextAreaElement>(
                            "textarea[data-chat-composer]",
                          );
                          if (!box) return;
                          box.focus();
                          box.setSelectionRange(box.value.length, box.value.length);
                        });
                      }}
                    />
                  ))}
                </div>
              )}
            </div>
          ) : !assistantsLoaded ? (
            /* Still asking. Claiming "no assistant selected" before the answer arrives
               tells someone with a dozen assistants that they have none. */
            <div className="flex flex-1 items-center justify-center px-6">
              <ReasoningText phrases={["Loading your assistants"]} />
            </div>
          ) : (
            <div className="flex flex-1 flex-col items-center justify-center gap-3 select-none px-6 text-center">
              <IconRobot size={64} stroke={1.25} className="opacity-40" />
              <div className="text-lg font-semibold">No assistant selected</div>
              <div className="max-w-xs text-sm text-muted-foreground">
                Choose an assistant or create a new one to start building a live dashboard.
              </div>
              <Button className="mt-1" onClick={() => onOpenSettings?.()}>
                Choose assistant
              </Button>
            </div>
          )
        ) : (
          (() => {
            let prevSide: "user" | "assistant" | null = null;
            // Skip activity items with no chips yet — they render nothing, so they
            // shouldn't claim the avatar row (which would strand the logo above the
            // "Working…" bubble until the first chip arrives). Likewise skip the
            // subagent panel until it has a group with actual content.
            const visible = items.filter((it) => {
              if (it.kind === "activity") return it.chips.length > 0;
              if (it.kind === "subagents")
                return it.groups.some((g) => g.chips.length > 0 || g.text);
              return true;
            });
            return visible.map((it) => {
              const side: "user" | "assistant" = it.kind === "user" ? "user" : "assistant";
              const showAvatar = side !== prevSide;
              prevSide = side;
              return (
                <Row key={it.id} side={side} showAvatar={showAvatar} logo={logo}>
                  <ItemView item={it} busy={busy} onApproveReview={approveReview} />
                </Row>
              );
            });
          })()
        )}
      </div>

      {/* Bottom composer once the conversation has started. */}
      {hasAssistant && items.length > 0 && composer("bottom")}
    </div>
  );
}

/**
 * The goal pill above the composer: what the agent is being graded against, how
 * that grading is going, and an × to drop it.
 */
function GoalPill({ goal, onClear }: { goal: Goal; onClear: () => void }) {
  const look = {
    active: { icon: IconTarget, label: "Goal", cls: "border-border text-muted-foreground" },
    grading: { icon: IconLoader2, label: "Checking goal", cls: "border-brand/50 text-brand" },
    revising: { icon: IconLoader2, label: "Another pass", cls: "border-brand/50 text-brand" },
    // emerald/amber rather than tokens: the palette has a brand accent and a
    // destructive, no pass/warn pair (same choice the vendored beUI rows make).
    met: { icon: IconCircleCheck, label: "Goal met", cls: "border-emerald-500/60 text-emerald-500" },
    stalled: {
      icon: IconAlertTriangle,
      label: "Goal not met",
      cls: "border-amber-500/60 text-amber-500",
    },
  }[goal.status];
  const Icon = look.icon;
  const spinning = goal.status === "grading" || goal.status === "revising";

  return (
    <div className="mb-1.5 flex flex-col gap-0.5">
      <span
        className={`inline-flex max-w-full items-center gap-1.5 self-start rounded-full border bg-panel-2 py-0.5 pr-1 pl-2 text-[11px] ${look.cls}`}
      >
        <Icon size={12} className={spinning ? "animate-spin" : undefined} />
        <span className="font-semibold uppercase tracking-wide">{look.label}</span>
        <span className="min-w-0 truncate font-normal text-foreground" title={goal.text}>
          {goal.text}
        </span>
        <button
          type="button"
          aria-label="Clear goal"
          title="Clear goal"
          className="rounded-full px-1 text-muted-foreground hover:text-foreground"
          onClick={onClear}
        >
          ×
        </button>
      </span>
      {/* Why it passed or stopped — only once there is a verdict to explain. */}
      {goal.note && (goal.status === "met" || goal.status === "stalled") ? (
        <span className="pl-2 text-[11px] text-muted-foreground">{goal.note}</span>
      ) : null}
    </div>
  );
}

/** Brand logo glyph: <img> for a URL/data-URI, an emoji span, else a Tabler robot. */
function LogoGlyph({ logo, size }: { logo?: string; size: number }) {
  const v = (logo || "").trim();
  if (/^(https?:|data:)/i.test(v)) {
    return (
      <img
        src={v}
        alt=""
        style={{ width: size, height: size }}
        className="rounded-md object-contain"
      />
    );
  }
  if (v) return <span style={{ fontSize: size * 0.9, lineHeight: 1 }}>{v}</span>;
  return <IconRobot size={size} stroke={1.25} />;
}

/** Circular chat avatar: a user glyph for the human, the brand logo for the assistant. */
function Avatar({ side, logo }: { side: "user" | "assistant"; logo?: string }) {
  if (side === "user") {
    return (
      <div className="flex h-9 w-9 items-center justify-center rounded-full bg-brand text-brand-foreground">
        <IconUser size={18} />
      </div>
    );
  }
  return (
    <div className="flex h-9 w-9 items-center justify-center overflow-hidden rounded-full border border-border bg-panel-2">
      <LogoGlyph logo={logo} size={22} />
    </div>
  );
}

/** A message row: fixed avatar slot on the left, content flowing to the right. */
function Row({
  side,
  showAvatar,
  logo,
  children,
}: {
  side: "user" | "assistant";
  showAvatar: boolean;
  logo?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex gap-3">
      <div className="w-9 shrink-0">{showAvatar ? <Avatar side={side} logo={logo} /> : null}</div>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

/* ---- Item renderers ---- */

function ItemView({
  item,
  busy,
  onApproveReview,
}: {
  item: Item;
  busy?: boolean;
  onApproveReview?: (id: string, value: Record<string, unknown>) => void;
}) {
  if (item.kind === "review") {
    // Once approved the editor is spent — the tool result is shown by its chip.
    if (item.done) {
      return (
        <div className="rounded-xl border border-border bg-panel-2 px-3 py-2 text-xs text-muted-foreground">
          {item.review.kind === "meeting_slots" ? "✓ Time confirmed" : "✓ Approved and sent"}
        </div>
      );
    }
    return (
      <ReviewCard
        review={item.review}
        busy={busy}
        onApprove={(v) => onApproveReview?.(item.id, v)}
      />
    );
  }
  if (item.kind === "user") {
    return (
      <MessageBubble variant="tint" align="start" animateIn className="text-sm leading-relaxed">
        {item.images?.length ? (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {item.images.map((src, i) => (
              <img
                key={i}
                src={src}
                alt="attachment"
                className="max-h-28 rounded-md border border-border object-cover"
              />
            ))}
          </div>
        ) : null}
        {item.docs?.length ? (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {item.docs.map((doc) => (
              <span
                key={doc.path}
                title={doc.path}
                className="inline-flex items-center gap-1 rounded border border-border px-1.5 py-0.5 text-[11px] text-muted-foreground"
              >
                <IconFileText size={12} />
                {doc.name}
              </span>
            ))}
          </div>
        ) : null}
        <MessageBubbleContent>{item.text}</MessageBubbleContent>
      </MessageBubble>
    );
  }
  if (item.kind === "activity") {
    if (!item.chips.length) return null;
    return (
      <div className="flex flex-col gap-1.5">
        {/* The whole burst folds into ONE openable row, whatever mix of tools it used.
            Grouping by tool name split a single stretch of work into a dozen rows the
            moment the agent alternated between reading and running, which is most of
            the time. Keyed on the first chip so the row keeps its component, and
            whether you opened it, as the stream adds to it - which is also what lets
            one row span several model turns. */}
        {item.chips.length === 1 ? (
          <ToolChip chip={item.chips[0]} />
        ) : (
          <ToolChipGroup key={item.chips[0].id} chips={item.chips} />
        )}
      </div>
    );
  }
  if (item.kind === "subagents") {
    const groups = item.groups.filter((g) => g.chips.length > 0 || g.text);
    if (!groups.length) return null;
    // Group parallel eval-dispatched subagents by their launching-eval root, so a
    // fan-out of N shows as ONE collapsible "Delegated to N subagents" card
    // instead of N identical rows. A lone task-tool subagent is its own group of
    // one and renders as a normal card.
    const byRoot: { root: string; members: SubagentGroup[] }[] = [];
    for (const g of groups) {
      const root = subagentRoot(g.key);
      const bucket = byRoot.find((b) => b.root === root);
      if (bucket) bucket.members.push(g);
      else byRoot.push({ root, members: [g] });
    }
    return (
      <div className="flex flex-col gap-1.5">
        {byRoot.map((b) =>
          b.members.length === 1 ? (
            <SubagentCard key={b.root} group={b.members[0]} />
          ) : (
            <SubagentFleet key={b.root} groups={b.members} />
          ),
        )}
      </div>
    );
  }
  if (item.kind === "feedback") {
    return <FeedbackRow runId={item.runId} workspace={item.workspace} />;
  }
  // assistant
  // The bare pre-stream placeholder → beUI's animated ReasoningText ("Thinking /
  // Reading the context / …") instead of a static "Working…".
  if (item.streaming && item.text === PLACEHOLDER_TEXT) {
    return <ReasoningText className="px-1 py-1.5" />;
  }
  // Real answers (final, or streaming once tokens arrive) render as markdown inside
  // beUI's StreamingResponse — it shows a streaming indicator and closes on
  // complete. Streamdown gracefully closes half-finished tables/bold/code fences so
  // partial output stays clean. Actions are suppressed; our FeedbackRow owns rating
  // (it posts to LangSmith with the run's id + workspace).
  const isAnswer = item.markdown || item.streaming;
  if (isAnswer) {
    return (
      <StreamingResponse
        status={item.streaming ? "streaming" : "complete"}
        copyText={item.text}
        showActions={false}
        contentClassName={PROSE_CLS}
      >
        {/* Word-by-word fade-in, which is what makes streamed text read as smooth
            rather than snapping the block to its new size on every chunk.
            
            TWO props are required and they do different jobs: `animated` configures the
            animation, but `isAnimating` is what actually adds the rehype plugin that
            annotates each word (it defaults to false, so `animated` alone is inert -
            and the animation is CSS, so streamdown/styles.css has to be imported too;
            see index.css). Tied to `streaming` so a finished answer is not re-animated
            when something else re-renders it.
            
            Word-level, not per character: per character on a long answer is a lot of
            simultaneous animation for no extra legibility. */}
        <Streamdown
          parseIncompleteMarkdown
          isAnimating={!!item.streaming}
          animated={ANSWER_ANIMATION}
        >
          {item.text}
        </Streamdown>
      </StreamingResponse>
    );
  }
  // Terminal non-answer text: errors, guard messages, "Dashboard ready.".
  return (
    <MessageBubble variant="soft" align="start" className="text-sm leading-relaxed">
      <MessageBubbleContent>{item.text}</MessageBubbleContent>
    </MessageBubble>
  );
}

/**
 * A collapsible "peer into subagent" card: its label, a live status (spinner
 * while running, ✓ once the run ends), and — when expanded — its streamed tool
 * chips and any text it produced. Distinct from the main answer bubble so
 * subagent work is visible but never mistaken for the assistant's reply.
 */
function SubagentCard({ group, index }: { group: SubagentGroup; index?: number }) {
  // Subagent cards start collapsed — the header (label, "invoked with",
  // step count, running/done) stays visible; expand to see the step chips.
  // `index` numbers a card within a fleet ("Subagent 1", "Subagent 2", …).
  const [open, setOpen] = useState(false);
  const running = !group.done;
  const count = group.chips.length;
  return (
    <div className="flex flex-col overflow-hidden rounded-lg border border-border bg-panel-2 text-xs text-muted-foreground">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 px-2.5 py-1.5 text-left hover:text-brand"
      >
        {open ? (
          <IconChevronDown size={14} className="shrink-0" />
        ) : (
          <IconChevronRight size={14} className="shrink-0" />
        )}
        <IconRobot size={15} className="shrink-0" stroke={2} />
        <span className="font-semibold text-foreground">
          {group.label}
          {index ? " " + index : ""}
        </span>
        {count > 0 && (
          <span className="text-[11px] text-muted-foreground">
            {count} step{count === 1 ? "" : "s"}
          </span>
        )}
        <span className="ml-auto flex items-center gap-1.5">
          {running ? (
            <IconLoader2 size={14} className="animate-spin opacity-70" />
          ) : (
            <span className="text-[11px] text-muted-foreground">✓ done</span>
          )}
        </span>
      </button>
      {group.invokedWith && (
        <div className="border-t border-border px-2.5 py-1 text-[11px] italic text-muted-foreground">
          ↳ invoked with:{" "}
          {group.invokedWith.length > 160
            ? group.invokedWith.slice(0, 160) + "…"
            : group.invokedWith}
        </div>
      )}
      {open && (
        <div className="flex flex-col gap-1.5 border-t border-border px-2.5 py-2">
          {group.chips.map((c) => (
            <ToolChip key={c.id} chip={c} />
          ))}
          {group.text && (
            <div className="whitespace-pre-wrap rounded-md border border-border bg-background px-2 py-1.5 text-[12px] leading-relaxed text-foreground">
              {group.text}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * A collapsed "fleet" card for a fan-out of eval-dispatched subagents that share
 * one launching eval. Shows the count + aggregate status on one line; expand to
 * see each subagent (numbered) as its own SubagentCard. Collapses the wall of N
 * identical "Subagent" rows a workflow turn used to produce.
 */
function SubagentFleet({ groups }: { groups: SubagentGroup[] }) {
  const [open, setOpen] = useState(false);
  const n = groups.length;
  const running = groups.filter((g) => !g.done).length;
  const allDone = running === 0;
  // A fan-out is usually one specialist run N ways; say which when they agree.
  const types = [...new Set(groups.map((g) => g.type).filter(Boolean))];
  const kind = types.length === 1 ? `${types[0]} ` : "";
  return (
    <div className="flex flex-col overflow-hidden rounded-lg border border-border bg-panel-2 text-xs text-muted-foreground">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 px-2.5 py-1.5 text-left hover:text-brand"
      >
        {open ? (
          <IconChevronDown size={14} className="shrink-0" />
        ) : (
          <IconChevronRight size={14} className="shrink-0" />
        )}
        <IconRobot size={15} className="shrink-0" stroke={2} />
        <span className="font-semibold text-foreground">
          Delegated to {n} {kind}subagents
        </span>
        <span className="ml-auto flex items-center gap-1.5">
          {allDone ? (
            <span className="text-[11px] text-muted-foreground">✓ all done</span>
          ) : (
            <>
              <span className="text-[11px] text-muted-foreground">{running} running</span>
              <IconLoader2 size={14} className="animate-spin opacity-70" />
            </>
          )}
        </span>
      </button>
      {open && (
        <div className="flex max-h-80 flex-col gap-1.5 overflow-auto border-t border-border px-2.5 py-2">
          {groups.map((g, i) => (
            <SubagentCard key={g.key} group={g} index={i + 1} />
          ))}
        </div>
      )}
    </div>
  );
}

function PresetButton({
  action,
  disabled,
  onClick,
}: {
  action: QuickAction;
  disabled?: boolean;
  onClick: () => void;
}) {
  const label = action.label || action.question;
  const i = label.indexOf(":");
  const cls =
    "cursor-pointer rounded-lg border border-border bg-panel-2 px-2.5 py-2 text-left text-[12.5px] text-foreground hover:border-brand hover:bg-brand/10 disabled:cursor-default disabled:opacity-60";
  // The click loads the composer rather than sending, so the title says so: a button
  // that looks like it asks the question but only fills a box needs to admit it.
  const title = "Put this in the message box to edit or send";
  if (i > 0) {
    return (
      <button type="button" title={title} className={cls} disabled={disabled} onClick={onClick}>
        <b className="text-[color:var(--brand-label)]">{label.slice(0, i + 1)}</b> {label.slice(i + 1).trim()}
      </button>
    );
  }
  return (
    <button type="button" title={title} className={cls} disabled={disabled} onClick={onClick}>
      {label}
    </button>
  );
}
