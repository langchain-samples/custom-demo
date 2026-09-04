/**
 * The agent graph as a floating inspector: draggable, resizable, and remembered.
 *
 *   ┌─ Agent graph ─────────────── ⤢ ✕ ┐
 *   │  lanes and call nodes, live       │
 *   └──────────────────────────────────◢┘
 *
 * NOT a Dialog, deliberately. Radix dialogs are modal - focus trap, inert background,
 * click-outside-to-close - so you could not type in the chat while watching the graph
 * fill in, and watching it fill in during a run is the entire reason this is not just a
 * tab. This is a plain positioned element with `aria-modal="false"`.
 *
 * It floats rather than taking layout space because it is an operator's tool, not part
 * of what the customer asked for: everything in the right-hand pane is output for the
 * audience, and this is introspection for the person driving. Dragging it into place is
 * also what makes it read that way.
 *
 * Position, size and open state all persist (see lib/floatingPanel), matching how the
 * chat rail and the settings panel already remember their widths.
 */
import { useCallback, useEffect, useState } from "react";
import { IconX } from "@tabler/icons-react";
import { AgentGraph } from "@/components/AgentGraph";
import type { ChipData } from "@/components/chat/ToolChip";
import type { GraphSubagent } from "@/lib/agentGraph";
import {
  clampRect,
  defaultRect,
  isPanelRect,
  MIN_H,
  MIN_W,
  type PanelRect,
} from "@/lib/floatingPanel";

// Suffixed so changing the DEFAULT placement actually reaches people who already have
// a stored one. A saved panel position is not worth preserving across a deliberate
// redesign of where it starts; anyone who liked theirs drags it back in a second.
const RECT_LS_KEY = "graphInspectorRect.v2";

export interface GraphInspectorProps {
  open: boolean;
  onClose: () => void;
  chips: ChipData[];
  subagents: GraphSubagent[];
  running: boolean;
  /** Assistant display name, for the graph's root node. */
  name: string;
}

/** Stored rect, clamped to this viewport, or the default. */
function readRect(): PanelRect {
  const fallback = defaultRect(window.innerWidth, window.innerHeight);
  try {
    const raw = window.localStorage.getItem(RECT_LS_KEY);
    if (!raw) return fallback;
    const parsed: unknown = JSON.parse(raw);
    if (!isPanelRect(parsed)) return fallback;
    return clampRect(parsed, window.innerWidth, window.innerHeight);
  } catch {
    return fallback;
  }
}

export function GraphInspector({
  open,
  onClose,
  chips,
  subagents,
  running,
  name,
}: GraphInspectorProps) {
  const [rect, setRect] = useState<PanelRect>(readRect);
  // Suppresses text selection and iframe/pointer interference mid-gesture.
  const [gesture, setGesture] = useState<"drag" | "resize" | null>(null);

  // Persist after the gesture settles rather than on every pointer move, matching how
  // the chat rail saves its width.
  useEffect(() => {
    if (gesture) return;
    try {
      window.localStorage.setItem(RECT_LS_KEY, JSON.stringify(rect));
    } catch {
      /* private window: the panel still works, it just will not be remembered */
    }
  }, [gesture, rect]);

  // A window that shrinks (or a laptop after an external monitor) can strand the panel
  // off-screen, so re-clamp rather than trusting the stored value forever.
  useEffect(() => {
    const onResize = () =>
      setRect((r) => clampRect(r, window.innerWidth, window.innerHeight));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // Escape closes, which is the one modal convention worth keeping.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  /** Shared pointer gesture, same shape as App's rail resizer. */
  const startGesture = useCallback(
    (kind: "drag" | "resize") => (e: React.PointerEvent) => {
      e.preventDefault();
      setGesture(kind);
      const startX = e.clientX;
      const startY = e.clientY;
      const from = rect;
      const onMove = (ev: PointerEvent) => {
        const dx = ev.clientX - startX;
        const dy = ev.clientY - startY;
        const next: PanelRect =
          kind === "drag"
            ? { ...from, x: from.x + dx, y: from.y + dy }
            : {
                ...from,
                w: Math.max(MIN_W, from.w + dx),
                h: Math.max(MIN_H, from.h + dy),
              };
        setRect(clampRect(next, window.innerWidth, window.innerHeight));
      };
      const onUp = () => {
        setGesture(null);
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [rect],
  );

  if (!open) return null;

  return (
    <div
      role="dialog"
      // Non-modal: the chat behind this stays usable, which is the point.
      aria-modal="false"
      aria-label="Agent graph"
      className={
        // z-40: above the app, below the Radix modals (Settings, Files, Evals) so
        // opening one of those does not fight this for the foreground.
        "fixed z-40 flex flex-col overflow-hidden rounded-xl border border-border bg-panel shadow-2xl print:hidden " +
        (gesture ? "select-none" : "")
      }
      style={{ left: rect.x, top: rect.y, width: rect.w, height: rect.h }}
    >
      <div
        onPointerDown={startGesture("drag")}
        className="flex cursor-grab items-center gap-2 border-b border-border px-3 py-2 active:cursor-grabbing"
      >
        <span className="text-xs font-semibold text-foreground">Agent graph</span>
        <span className="truncate text-[11px] text-muted-foreground">
          every tool call this turn, by what it touched
        </span>
        {running && (
          <span className="shrink-0 text-[11px] text-muted-foreground">live</span>
        )}
        <button
          type="button"
          onClick={onClose}
          aria-label="Close the agent graph"
          className="ml-auto shrink-0 rounded p-0.5 text-muted-foreground hover:text-brand"
        >
          <IconX size={14} />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {chips.length === 0 ? (
          <p className="m-0 p-4 text-xs text-muted-foreground">
            Nothing yet. Ask a question and the lanes fill in as the agent works.
          </p>
        ) : (
          <AgentGraph chips={chips} subagents={subagents} running={running} name={name} />
        )}
      </div>

      {/* Resize grip. Bottom-right only: two axes from one corner is enough, and edge
          handles on a floating panel are easy to grab by accident while dragging. */}
      <div
        onPointerDown={startGesture("resize")}
        title="Drag to resize"
        className="absolute right-0 bottom-0 h-4 w-4 cursor-nwse-resize"
      />
    </div>
  );
}
