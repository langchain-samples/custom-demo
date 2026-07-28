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
import { IconRobot, IconLoader2, IconUser } from "@tabler/icons-react";
import type { QuickAction, ReviewInterrupt, RunContext, ThreadMessage, Widget } from "@/lib/api";
import { ensureThread, resetThread, runStream } from "@/lib/api";
import { ReviewCard } from "@/components/chat/ReviewCard";
import { IconArrowUp } from "@tabler/icons-react";
import { Button } from "@/components/ui/button";
import { ToolChip, type ChipData } from "@/components/chat/ToolChip";
import { FeedbackRow } from "@/components/chat/FeedbackRow";
import {
  chipArgSummary,
  contentToText,
  toolCallKey,
  widgetFromArgs,
  widgetLooksComplete,
} from "@/components/chat/helpers";

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
  /** Receives each widget as it flushes (progressive), for the dashboard pane. */
  onWidget: (widget: Widget) => void;
  /** Called at the start of every send so the dashboard pane can clear itself. */
  onResetDashboard?: () => void;
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
}
interface ActivityItem {
  kind: "activity";
  id: string;
  chips: ChipData[];
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
type Item = UserItem | ActivityItem | AssistantItem | FeedbackItem | ReviewItem;


export default function ChatPanel({
  assistantId,
  presets = [],
  getRunContext,
  onWidget,
  onResetDashboard,
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

  // Keep the log pinned to the newest message.
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [items]);

  const patchItem = (id: string, fn: (it: Item) => Item) =>
    setItems((prev) => prev.map((it) => (it.id === id ? fn(it) : it)));

  /**
   * Run one turn: either a new question, or a resume of a run paused at a
   * human-review interrupt. Both share the whole streaming pipeline; a resume
   * simply carries `resume` instead of `messages` and does not clear the
   * dashboard (it is a continuation of the same turn).
   */
  const runTurn = async (opts: { question?: string; resume?: unknown }) => {
    const { question, resume } = opts;
    const isResume = resume !== undefined;
    if (busyRef.current) return;
    if (!isResume && !question) return;

    if (!isResume) onResetDashboard?.();
    busyRef.current = true;
    setBusy(true);

    const activityId = nextId();
    const bubbleId = nextId();
    setItems((prev) => [
      ...prev,
      ...(question ? [{ kind: "user" as const, id: nextId(), text: question }] : []),
      { kind: "activity", id: activityId, chips: [] },
      { kind: "assistant", id: bubbleId, text: "Working…", streaming: true, markdown: false },
    ]);

    // Per-run mutable stream state (persists across the whole for-await loop).
    const chipOrder: string[] = [];
    const chipMap: Record<string, ChipData> = {};
    const wOrder: string[] = [];
    const wLatest: Record<string, Widget> = {};
    const wFlushed = new Set<string>();
    // langgraph_node per message id, from `messages/metadata` events. The main
    // agent's messages are node "model"; a tool's internal LLM calls (e.g. the
    // synthetic data source) are node "tools" — we must NOT render those as chat.
    const nodeById: Record<string, string> = {};
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
          const id = toolCallKey(msg.id, tc);
          const name = tc.name || "";
          const args = tc.args || {};
          if (name === "push_widget") {
            wLatest[id] = widgetFromArgs(args);
            if (!wOrder.includes(id)) wOrder.push(id);
            // Flush every widget except the one still streaming (last in order).
            for (let i = 0; i < wOrder.length - 1; i++) flushWidget(wOrder[i]);
          } else {
            const summary = chipArgSummary(name, args);
            if (!chipMap[id]) {
              chipMap[id] = { id, name, arg: summary, result: null };
              chipOrder.push(id);
            } else {
              chipMap[id] = { ...chipMap[id], arg: summary };
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

    const controller = new AbortController();
    abortRef.current = controller;

    const runContext = getRunContext();
    try {
      const tid = await ensureThread();
      for await (const { event, data } of runStream({
        threadId: tid,
        assistantId,
        ...(isResume ? { resume } : { messages: [{ role: "user", content: question! }] }),
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
          const d = parsed as { run_id?: string };
          if (d && d.run_id) runId = d.run_id;
          continue;
        }
        if (event === "error") {
          const d = parsed as { error?: string; message?: string };
          errorMsg = (d && (d.error || d.message)) || "run error";
          continue;
        }
        if (event === "messages/metadata") {
          // { "<message_id>": { metadata: { langgraph_node, ... } } }
          const d = parsed as Record<string, { metadata?: { langgraph_node?: string } }>;
          if (d && typeof d === "object") {
            for (const [mid, info] of Object.entries(d)) {
              const n = info?.metadata?.langgraph_node;
              if (n) nodeById[mid] = n;
            }
          }
          continue;
        }
        if (event === "updates") {
          // A tool called interrupt() — the run is now paused awaiting a human.
          const d = parsed as { __interrupt__?: Array<{ value?: ReviewInterrupt }> };
          const value = d?.__interrupt__?.[0]?.value;
          if (value && typeof value === "object") interrupt = value;
          continue;
        }
        if (event === "messages/partial" || event === "messages/complete") {
          const msg = (Array.isArray(parsed) ? parsed[0] : parsed) as ThreadMessage;
          onStreamMessage(msg);
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
    void runTurn({ question });
  };

  /** Human approved a paused artifact — resume the run with their version. */
  const approveReview = (itemId: string, value: Record<string, unknown>) => {
    setItems((prev) =>
      prev.map((it) => (it.id === itemId && it.kind === "review" ? { ...it, done: true } : it)),
    );
    void runTurn({ resume: value });
  };

  const submitCurrent = () => {
    const q = input;
    setInput("");
    send(q);
  };

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    submitCurrent();
  };

  // Enter sends; Shift+Enter inserts a newline (standard chat-composer behaviour).
  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!busy && input.trim()) submitCurrent();
    }
  };

  const heroPlaceholder = industry
    ? `Ask me anything about ${industry}…`
    : "Ask a question…";

  const composer = (variant: "hero" | "bottom") => (
    <form
      onSubmit={onSubmit}
      className={variant === "hero" ? "w-full" : "border-t border-border px-3.5 py-3"}
    >
      {/* Rounded, auto-growing composer. The hero variant (shown only before the
          first prompt) carries a resting brand-primary glow; the bottom variant is
          plain. Both intensify the glow on focus. */}
      <div
        className={
          "flex items-end gap-2 rounded-2xl border bg-panel-2 px-3 py-2 transition-[box-shadow,border-color] " +
          (variant === "hero"
            ? "border-[color-mix(in_oklch,var(--brand-primary)_55%,var(--border))] shadow-[0_0_22px_-4px_color-mix(in_oklch,var(--brand-primary)_45%,transparent)] focus-within:border-[var(--brand-primary)] focus-within:shadow-[0_0_28px_-2px_color-mix(in_oklch,var(--brand-primary)_55%,transparent)]"
            : "border-input shadow-sm focus-within:border-[var(--brand-primary)] focus-within:shadow-[0_0_0_3px_color-mix(in_oklch,var(--brand-primary)_28%,transparent)]")
        }
      >
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          rows={1}
          placeholder={variant === "hero" ? heroPlaceholder : "Ask a question…"}
          autoComplete="off"
          className="field-sizing-content max-h-40 min-h-[28px] flex-1 resize-none self-center bg-transparent py-1 text-sm leading-relaxed outline-none placeholder:text-muted-foreground"
        />
        <Button
          type="submit"
          size="icon"
          disabled={busy || !input.trim()}
          aria-label="Send"
          className="h-8 w-8 shrink-0 rounded-full"
        >
          <IconArrowUp className="h-4 w-4" stroke={2.5} />
        </Button>
      </div>
    </form>
  );

  return (
    <div className="flex min-h-0 w-full flex-1 flex-col bg-panel">
      <div ref={logRef} className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-[18px]">
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
            // "Working…" bubble until the first chip arrives).
            const visible = items.filter(
              (it) => !(it.kind === "activity" && it.chips.length === 0),
            );
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
      <div className="rounded-xl bg-panel-2 px-3 py-2.5 text-sm leading-relaxed text-foreground">
        {item.text}
      </div>
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
  if (item.kind === "feedback") {
    return <FeedbackRow runId={item.runId} workspace={item.workspace} />;
  }
  // assistant
  const base = "text-sm leading-relaxed text-foreground";
  // Render markdown for real answers — both the final message AND live while it
  // streams (Streamdown gracefully closes half-finished tables/bold/code fences,
  // so partial output stays clean instead of showing raw ** and |---| syntax).
  const isAnswer = item.markdown || (item.streaming && item.text !== "Working…");
  if (isAnswer) {
    return (
      <div
        className={
          base +
          " rounded-xl bg-panel-2 px-3 py-2.5" +
          " [&_p]:my-1.5 [&_ul]:my-1.5 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:my-1.5 [&_ol]:list-decimal [&_ol]:pl-5 [&_li]:my-0.5" +
          " [&_h1]:mb-1 [&_h1]:mt-2 [&_h1]:text-base [&_h1]:font-bold [&_h2]:mb-1 [&_h2]:mt-2 [&_h2]:text-[15px] [&_h2]:font-bold [&_h3]:mb-1 [&_h3]:mt-2 [&_h3]:font-semibold" +
          " [&_strong]:font-semibold [&_a]:text-[color:var(--brand-label)] [&_a]:underline" +
          " [&_code]:rounded [&_code]:bg-background [&_code]:px-1 [&_code]:py-px [&_code]:font-mono [&_code]:text-[12px]" +
          " [&_table]:my-2 [&_table]:w-full [&_table]:border-collapse [&_table]:text-xs" +
          " [&_th]:border-b [&_th]:border-border [&_th]:px-2 [&_th]:py-1 [&_th]:text-left [&_th]:font-semibold" +
          " [&_td]:border-b [&_td]:border-border/50 [&_td]:px-2 [&_td]:py-1"
        }
      >
        <Streamdown parseIncompleteMarkdown>{item.text}</Streamdown>
      </div>
    );
  }
  return (
    <div className={base + " rounded-xl bg-panel-2 px-3 py-2.5"}>
      {item.streaming && item.text === "Working…" ? (
        <span className="inline-flex items-center gap-1.5">
          <IconLoader2 size={15} className="animate-spin [animation-duration:0.6s]" />
          {item.text}
        </span>
      ) : (
        item.text
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
