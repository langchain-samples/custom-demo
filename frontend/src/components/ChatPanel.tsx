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
import { useEffect, useRef, useState } from "react";
import { Streamdown } from "streamdown";
import { IconRobot, IconLoader2, IconPhoto, IconUser } from "@tabler/icons-react";
import type { QuickAction, ReviewInterrupt, RunContext, ThreadMessage, Widget } from "@/lib/api";
import { ensureThread, resetThread, runStream } from "@/lib/api";
import { PROSE_CLS } from "@/lib/markdown";
import { ReviewCard } from "@/components/chat/ReviewCard";
import { Button } from "@/components/motion/button";
import { ToolChip, type ChipData } from "@/components/chat/ToolChip";
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
  toolCallKey,
  widgetFromArgs,
  widgetLooksComplete,
} from "@/components/chat/helpers";
import {
  IMAGE_MIME_TYPES,
  imageContent,
  readImageAttachment,
  type ImageAttachment,
} from "@/lib/api";
import {
  effectiveNamespace,
  isSubagentNamespace,
  subagentIdentity,
  subagentRoot,
} from "@/lib/streamEvent";

export interface ChatPanelProps {
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
  /** Open the settings sheet (from the "Choose assistant" empty-state CTA). */
  onOpenSettings?: () => void;
}

/* ---- Internal message-list model ---- */

interface UserItem {
  kind: "user";
  id: string;
  text: string;
  /** Data URLs for images sent with this turn, so the log shows what the agent saw. */
  images?: string[];
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
  /** Human-readable name ("Subagent"). */
  label: string;
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


export default function ChatPanel({
  assistantId,
  presets = [],
  getRunContext,
  onWidget,
  guard,
  resetKey,
  logo,
  industry,
  hasAssistant,
  onOpenSettings,
}: ChatPanelProps) {
  const [items, setItems] = useState<Item[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);

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
   * Run one turn: either a new question, or a resume of a run paused at a
   * human-review interrupt. Both share the whole streaming pipeline; a resume
   * simply carries `resume` instead of `messages` and does not clear the
   * dashboard (it is a continuation of the same turn).
   */
  const runTurn = async (opts: {
    question?: string;
    resume?: unknown;
    images?: ImageAttachment[];
  }) => {
    const { question, resume, images = [] } = opts;
    const isResume = resume !== undefined;
    if (busyRef.current) return;
    if (!isResume && !question) return;

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
            },
          ]
        : []),
      { kind: "activity", id: activityId, chips: [] },
      { kind: "subagents", id: subagentId, groups: [] },
      { kind: "assistant", id: bubbleId, text: "Working…", streaming: true, markdown: false },
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
    // Main-agent `task` tool-call args, keyed by tool_call id, so a subagent card
    // can show what its parent dispatched it with (the task description). The
    // subagent's namespace key is `tools:<that id>`.
    const taskArgs: Record<string, string> = {};
    let answer = "";
    let runId: string | null = null;
    let errorMsg: string | null = null;
    let interrupt: ReviewInterrupt | null = null;

    const syncChips = () =>
      patchItem(activityId, (it) =>
        it.kind === "activity" ? { ...it, chips: chipOrder.map((id) => ({ ...chipMap[id] })) } : it,
      );
    const setBubble = (patch: Partial<Omit<AssistantItem, "kind" | "id">>) =>
      patchItem(bubbleId, (it) => (it.kind === "assistant" ? { ...it, ...patch } : it));

    const flushWidget = (id: string) => {
      const w = wLatest[id];
      if (w && !wFlushed.has(id) && widgetLooksComplete(w)) {
        wFlushed.add(id);
        // Widgets ACCUMULATE across turns — the dashboard is a persistent canvas for
        // the whole conversation, cleared only by New Chat / assistant switch. So an
        // "add a chart" follow-up appends (both charts stay), matching what the agent
        // tells the user, and a text-only turn leaves the dashboard untouched.
        onWidget(w);
        if (!answer) setBubble({ text: "Building your dashboard…" });
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
            const id = toolCallKey(msg.id, tc);
            wLatest[id] = widgetFromArgs(args);
            if (!wOrder.includes(id)) wOrder.push(id);
            // Flush every widget except the one still streaming (last in order).
            for (let i = 0; i < wOrder.length - 1; i++) flushWidget(wOrder[i]);
          } else {
            // A chip MUST be keyed by the real tool_call id so the tool's result
            // (ToolMessage.tool_call_id) can match and clear its spinner. Skip
            // partial stream frames that don't carry the id yet — keying a chip on a
            // fallback would create a phantom that never receives a result and spins
            // forever (an ever-climbing timer), duplicating the real chip.
            const id = tc.id;
            if (!id) continue;
            // Remember a task dispatch's description so its subagent card can show
            // "what it was invoked with" (subagent namespace = tools:<this id>).
            if (name === "task") {
              const a = args as { description?: string; subagent_type?: string };
              taskArgs[id] = a.description || a.subagent_type || "";
            }
            const summary = chipArgSummary(name, args);
            // Carry the source/command a code-running tool executed (eval's `code`,
            // execute's `command`) so the expanded chip can show it highlighted.
            // Accumulates across partial frames just like the summary.
            const ci = chipCode(name, args);
            if (!chipMap[id]) {
              chipMap[id] = { id, name, arg: summary, result: null, code: ci?.code, codeLang: ci?.lang };
              chipOrder.push(id);
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
        // Final answer = a MAIN-agent AI message with text, no tools. Exclude
        // tool-internal LLM output (node "tools") — it must not leak into chat.
        const text = contentToText(msg.content);
        if (text && tcs.length === 0 && node !== "tools") {
          answer = text; // partial content is cumulative per message id
          setBubble({ text: answer, streaming: true, markdown: false });
        }
      } else if (msg.type === "tool" && msg.name !== "push_widget") {
        const cid = msg.tool_call_id;
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

    // Rebuild the SubagentItem from subState. `done` stays false while streaming;
    // the finally block freezes it (and any pending chip) once the run ends.
    const syncSubagents = () =>
      patchItem(subagentId, (it) =>
        it.kind === "subagents"
          ? {
              ...it,
              groups: subOrder.map((k) => ({
                key: k,
                label: subState[k].label,
                chips: subState[k].chipOrder.map((id) => ({ ...subState[k].chipMap[id] })),
                text: subState[k].text,
                // key is `tools:<task_call_id>`; look up what it was dispatched with.
                invokedWith: taskArgs[k.startsWith("tools:") ? k.slice(6) : k] || undefined,
                done: false,
              })),
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
          : { messages: [{ role: "user", content: imageContent(question!, images) }] }),
        context: runContext,
        signal: controller.signal,
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
        if (event === "error") {
          // Only surface root-graph errors in the main bubble; a subagent error
          // must not leak into the main answer.
          if (!isSubagentNamespace(namespace)) {
            const d = parsed as { error?: string; message?: string };
            errorMsg = (d && (d.error || d.message)) || "run error";
          }
          continue;
        }
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
        const spoke = !!answer;
        setBubble({ streaming: false, markdown: false });
        setItems((prev) => [
          ...prev.filter((it) => spoke || it.id !== bubbleId),
          { kind: "review", id: nextId(), review: pending, done: false },
        ]);
        return;
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
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        setBubble({
          streaming: false,
          markdown: false,
          text: "⚠️ Request failed: " + (e as Error).message,
        });
      }
    } finally {
      busyRef.current = false;
      setBusy(false);
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

  /** New question from the composer or a quick action. */
  const send = (raw: string) => {
    const question = (raw || "").trim();
    if (!question || busyRef.current) return;
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
    setAttachError("");
    void runTurn({ question, images });
  };

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
  const composer = (variant: "hero" | "bottom") => (
    <div
      className={
        variant === "hero"
          ? "w-full rounded-2xl shadow-[0_0_16px_-5px_color-mix(in_oklch,var(--brand-primary)_38%,transparent)]"
          : "border-t border-border px-3.5 py-3"
      }
    >
      {/* Attached images ride with the next turn. The agent can SEE these, unlike a
          file uploaded to its VM — which it can only open with code. */}
      {attached.length || attachError ? (
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
        // Paste is how a screenshot actually arrives; the picker below is the fallback.
        onPaste={(e) => {
          const files = Array.from(e.clipboardData?.files ?? []);
          if (files.length) {
            e.preventDefault();
            void attach(files);
          }
        }}
      />
      <div className="mt-1 flex items-center gap-2">
        <input
          ref={imagePicker}
          type="file"
          accept={IMAGE_MIME_TYPES.join(",")}
          multiple
          className="hidden"
          onChange={(e) => {
            void attach(e.target.files);
            e.target.value = "";
          }}
        />
        <button
          type="button"
          className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
          onClick={() => imagePicker.current?.click()}
        >
          <IconPhoto size={13} /> Attach image
        </button>
        <span className="text-[11px] text-muted-foreground">or paste a screenshot</span>
      </div>
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
                    <PresetButton key={i} action={p} disabled={busy} onClick={() => send(p.question)} />
                  ))}
                </div>
              )}
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
        <MessageBubbleContent>{item.text}</MessageBubbleContent>
      </MessageBubble>
    );
  }
  if (item.kind === "activity") {
    if (!item.chips.length) return null;
    return (
      <div className="flex flex-col gap-1.5">
        {item.chips.map((c) => (
          <ToolChip key={c.id} chip={c} />
        ))}
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
  if (item.streaming && item.text === "Working…") {
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
        <Streamdown parseIncompleteMarkdown>{item.text}</Streamdown>
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
        <span className="font-semibold text-foreground">Delegated to {n} subagents</span>
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
  if (i > 0) {
    return (
      <button type="button" className={cls} disabled={disabled} onClick={onClick}>
        <b className="text-[color:var(--brand-label)]">{label.slice(0, i + 1)}</b> {label.slice(i + 1).trim()}
      </button>
    );
  }
  return (
    <button type="button" className={cls} disabled={disabled} onClick={onClick}>
      {label}
    </button>
  );
}
