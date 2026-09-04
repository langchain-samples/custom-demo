/**
 * Pure helpers + layout constants for graph mode. Kept OUT of AgentGraph.tsx on
 * purpose: a component file that also exports plain functions breaks React Fast
 * Refresh, and Vite then does a full `invalidate` instead of a hot swap. During a
 * live demo that reload wipes ChatPanel's state mid-run — the chat empties while
 * the dashboard (held in App) survives. Same split as `lib/streamEvent.ts` and
 * `lib/branding.ts`, so this is also the repo's existing convention.
 */

import type { ChipData } from "@/components/chat/ToolChip";

/** One dispatched subagent's live work, mirrored from ChatPanel's own model. */
export interface GraphSubagent {
  key: string;
  label: string;
  type?: string;
  chips: ChipData[];
  invokedWith?: string;
  done: boolean;
}

/**
 * One question's tool activity, mirrored out of ChatPanel so the graph can draw it.
 * Read-only by contract: nothing here feeds back into a run.
 */
export interface ActivityState {
  chips: ChipData[];
  subagents: GraphSubagent[];
  running: boolean;
}

export interface Lane {
  id: string;
  label: string;
  hint: string;
}

/**
 * The lanes, in the order the agent actually moves through them. Ordering is
 * editorial, not derived: it is the story the graph is meant to tell.
 */
export const LANES: Lane[] = [
  { id: "plan", label: "Plan", hint: "todo list the agent wrote itself" },
  { id: "skills", label: "Skills", hint: "codified workflow it consults first" },
  { id: "sandbox", label: "Sandbox", hint: "its own VM: shell and Python" },
  { id: "data", label: "Data", hint: "systems of record" },
  { id: "web", label: "Web", hint: "external lookup" },
  { id: "human", label: "Human", hint: "pauses for a person" },
  { id: "delegate", label: "Delegate", hint: "hands work to a subagent" },
  { id: "output", label: "Output", hint: "widgets on the dashboard" },
];

/**
 * Which lane a call belongs to. Keyed on tool name first, then on the argument,
 * because `read_file` is a skill read or a sandbox read depending on the path —
 * and that distinction is the whole point of the Skills lane.
 */
export function laneFor(chip: ChipData): string {
  const name = (chip.name || "").toLowerCase();
  const arg = chip.arg || "";
  if (name === "write_todos") return "plan";
  if (name === "push_widget") return "output";
  if (name === "datasearch" || name === "list_data_sources") return "data";
  if (name === "web_search") return "web";
  if (name === "draft_email" || name === "suggest_meeting_times" || name === "ask_user")
    return "human";
  if (name === "task" || name === "eval") return "delegate";
  if (name === "read_file" && /\/skills?\//.test(arg)) return "skills";
  return "sandbox";
}

/**
 * Short label for a node: the most identifying thing available.
 *
 * `execute` carries its shell/Python in `code` and leaves `arg` empty, so a label
 * built from `arg` alone renders as the bare word "execute" and the sandbox work
 * looks hidden. Fall back to the first real line of `code`, which is what the
 * agent actually ran.
 */
export function nodeLabel(chip: ChipData, maxChars = DEFAULT_LABEL_CHARS): string {
  const arg = (chip.arg || "").trim();
  if (arg) {
    // A path: keep the last two segments, which is what identifies it.
    if (arg.includes("/")) {
      const parts = arg.split(/[?\s]/)[0].split("/").filter(Boolean);
      if (parts.length > 1) return truncate(parts.slice(-2).join("/"), maxChars);
    }
    return truncate(arg, maxChars);
  }
  const code = (chip.code || "").trim();
  if (code) {
    // Skip heredoc/shebang noise and blank lines to reach the real first command.
    const line = code
      .split("\n")
      .map((l) => l.trim())
      .find((l) => l && !l.startsWith("#") && !l.startsWith("<<") && l !== "EOF");
    if (line) return truncate(line, maxChars);
  }
  return chip.name;
}

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max - 1) + "…" : s;
}

/** Rough size of a tool's output, for showing that a step did real work. */
export function resultSize(chip: ChipData): string {
  if (chip.result === null) return "";
  const lines = chip.result.split("\n").filter(Boolean).length;
  if (lines > 1) return `${lines} lines`;
  const n = chip.result.length;
  return n > 0 ? `${n} chars` : "empty";
}

/** A call is pending until its tool result lands (or the run stops). */
export const isPending = (c: ChipData) => c.result === null && !c.stopped;

/* ---- Geometry ------------------------------------------------------------ */

// The lane column is wide enough for its hint (the longest is 35 characters, about
// 161px at fontSize 9), because SVG text does not wrap and a clipped explanation is
// worse than none. The old widths were tuned to fit the right-hand pane without
// horizontal scrolling; the graph lives in a resizable floating panel now, which
// scrolls, so fitting a fixed pane is no longer the constraint.
export const ROOT_X = 16;
export const ROOT_W = 132;
export const LANE_X = 176;
export const LANE_W = 190;
export const NODE_X = 394;
/** Narrowest the call column goes; a wider panel grows it (see nodeWidthFor). */
export const NODE_W = 236;

/** Trailing margin right of the call column. */
const GUTTER = 16;

/**
 * How wide the call column should be inside a panel of `panelW`.
 *
 * The column used to be fixed at 236px, so a resized panel just grew empty space to the
 * right while labels stayed clipped to 23 characters - two `data/competitor_produc…`
 * nodes were indistinguishable even with room to tell them apart.
 */
export function nodeWidthFor(panelW: number): number {
  if (!Number.isFinite(panelW) || panelW <= 0) return NODE_W;
  return Math.max(NODE_W, Math.floor(panelW) - NODE_X - GUTTER);
}

/** Total drawing width for a given call-column width. */
export function graphWidthFor(nodeW: number): number {
  return NODE_X + nodeW + GUTTER;
}

/**
 * Characters that fit in a call node of `nodeW`.
 *
 * The label starts 26px in (past the status dot) and the size readout is right-aligned
 * in roughly the last 70px, leaving `nodeW - 96`. At fontSize 11.5 a sans character
 * averages about 6px, which is where the original 236px column's 23 characters came
 * from - this just stops that number being a constant.
 */
export function labelCharsFor(nodeW: number): number {
  return Math.max(12, Math.floor((nodeW - 96) / 6));
}

/** What a 236px column fits, kept as the default so callers need not pass a width. */
export const DEFAULT_LABEL_CHARS = 23;
export const ROW_H = 34;
export const LANE_GAP = 16;

/** A cubic bezier from the right edge of one box to the left edge of another. */
export function edge(x1: number, y1: number, x2: number, y2: number): string {
  const mid = x1 + (x2 - x1) / 2;
  return `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`;
}
