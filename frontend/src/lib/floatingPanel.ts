/**
 * Geometry for the floating graph inspector.
 *
 * Split out of the component because the clamp is the part that bites: a position saved
 * on a 1440px external monitor is off-screen on a laptop, and an inspector you cannot
 * reach is worse than no inspector. That is a pure function of two rectangles, so it is
 * here and it is tested, rather than buried in a drag handler.
 */

/** Where the panel is, in viewport pixels. */
export interface PanelRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Smallest useful size. The graph's own node column is 552px wide (see agentGraph). */
export const MIN_W = 380;
export const MIN_H = 220;

/** Kept on screen by at least this much, so there is always something to grab. */
const EDGE_KEEP = 80;

/** Margin from the viewport edges for the default placement. */
const MARGIN = 24;

/**
 * Default: bottom-RIGHT.
 *
 * Over the dashboard rather than the chat, which is the opposite of what I first
 * guessed. The chat is what a presenter reads from and talks over while a turn runs, so
 * covering it is the more costly choice; the dashboard is mostly empty until widgets
 * land, and by then you have usually moved or closed the panel.
 */
export function defaultRect(viewW: number, viewH: number): PanelRect {
  // 680 fits the graph's own width (NODE_X + NODE_W + 16 = 646) without scrolling.
  const w = Math.min(680, Math.max(MIN_W, viewW - 2 * MARGIN));
  // Tall enough for the lanes a real turn produces: eight of them at ROW_H plus their
  // call nodes overflowed 460 and made the panel scroll from the first question.
  const h = Math.min(600, Math.max(MIN_H, viewH - 160));
  return {
    x: Math.max(MARGIN, viewW - w - MARGIN),
    y: Math.max(72, viewH - h - MARGIN),
    w,
    h,
  };
}

/**
 * A rect that is definitely reachable in a viewport this size.
 *
 * Size is clamped first, then position, because clamping position against a width that
 * is itself too big pins the panel to the left edge for no reason. Both axes keep a
 * strip on screen rather than the whole panel: dragging most of it off the right edge is
 * a legitimate thing to want, losing the title bar is not.
 */
export function clampRect(rect: PanelRect, viewW: number, viewH: number): PanelRect {
  const w = Math.max(MIN_W, Math.min(rect.w, Math.max(MIN_W, viewW - 16)));
  const h = Math.max(MIN_H, Math.min(rect.h, Math.max(MIN_H, viewH - 16)));
  const x = Math.max(EDGE_KEEP - w, Math.min(rect.x, viewW - EDGE_KEEP));
  // Never above 0: the drag handle is the top edge, and a negative y puts it under the
  // app header where it cannot be grabbed.
  const y = Math.max(0, Math.min(rect.y, viewH - EDGE_KEEP));
  return { x, y, w, h };
}

/** Whether a stored value is actually a rect, before it reaches the clamp. */
export function isPanelRect(v: unknown): v is PanelRect {
  if (!v || typeof v !== "object") return false;
  const r = v as Record<string, unknown>;
  return ["x", "y", "w", "h"].every((k) => typeof r[k] === "number" && Number.isFinite(r[k]));
}
