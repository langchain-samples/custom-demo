/**
 * Pure helpers for the chat streaming loop, ported from the original static
 * app.js (contentToText, widgetLooksComplete, tool-chip metadata + arg summary).
 * Kept framework-agnostic so ChatPanel and any tests can share them.
 */
import type { ComponentType } from "react";
import {
  IconSearch,
  IconDatabase,
  IconChecklist,
  IconRobot,
  IconChartBar,
  IconTool,
} from "@tabler/icons-react";
import type { MessageContent, ToolCall, Widget } from "@/lib/api";

/** A Tabler icon component (size/stroke/className props). */
export type TablerIcon = ComponentType<{ size?: number | string; className?: string; stroke?: number }>;

/** Flatten a message's content (string or content-block array) to plain text. */
export function contentToText(content: MessageContent | undefined): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((b) =>
        b && typeof b === "object" && b.type === "text"
          ? b.text || ""
          : typeof b === "string"
            ? b
            : "",
      )
      .join("");
  }
  return "";
}

/**
 * Only render a widget once its parsed args look complete (streamed partials
 * fill in incrementally). Prevents 1-point charts / half-built tables from
 * flashing on screen. Byte-for-byte the original `widgetLooksComplete`.
 */
export function widgetLooksComplete(w: Widget | null | undefined): boolean {
  if (!w || !w.type) return false;
  const pts = (s: unknown): boolean =>
    Array.isArray(s) &&
    s.length > 0 &&
    Array.isArray(s[0].points) &&
    s[0].points.length > 0 &&
    s[0].points.every(
      (p: unknown) =>
        !!p &&
        typeof p === "object" &&
        (p as { label?: unknown }).label !== undefined &&
        (p as { value?: unknown }).value !== undefined,
    );
  switch (w.type) {
    case "kpi":
      return !!w.title && w.value !== undefined && w.value !== "";
    case "bar":
    case "line":
    case "pie":
      return !!w.title && pts(w.series);
    case "table":
      return (
        !!w.title &&
        Array.isArray(w.columns) &&
        w.columns.length > 0 &&
        Array.isArray(w.rows) &&
        w.rows.length > 0
      );
    case "text":
      return !!w.title && !!w.content;
    default:
      return false;
  }
}

/** Icon + label shown on each tool "chip". Mirrors the original TOOL_META. */
export const TOOL_META: Record<string, { icon: TablerIcon; label: string }> = {
  datasearch: { icon: IconSearch, label: "Searched reports" },
  query_sql: { icon: IconDatabase, label: "Ran SQL query" },
  write_todos: { icon: IconChecklist, label: "Planned steps" },
  task: { icon: IconRobot, label: "Delegated to subagent" },
  push_widget: { icon: IconChartBar, label: "Added widget" },
};

export function toolMeta(name: string): { icon: TablerIcon; label: string } {
  return TOOL_META[name] || { icon: IconTool, label: name };
}

/**
 * The one-line summary shown on a tool chip's arg pill. For datasearch/query_sql
 * it's the `query` arg; otherwise a truncated JSON of the args. Matches app.js.
 */
export function chipArgSummary(name: string, args: Record<string, unknown>): string {
  if (name === "datasearch" || name === "query_sql") return String(args.query || "");
  return JSON.stringify(args).slice(0, 120);
}

/** Extract the `widget` spec from a push_widget tool call's args. */
export function widgetFromArgs(args: Record<string, unknown>): Widget {
  const w = (args.widget ?? args) as Widget;
  return w;
}

/** Convenience: the tool_call id used to key chips/widgets, with a fallback. */
export function toolCallKey(msgId: string | undefined, tc: ToolCall): string {
  return tc.id || `${msgId || ""}:${tc.name || ""}`;
}
