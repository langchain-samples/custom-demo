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
  IconMail,
  IconCalendarEvent,
  IconWorldSearch,
  IconFolder,
  IconFileText,
  IconFilePencil,
  IconCode,
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
  // Core
  datasearch: { icon: IconSearch, label: "Searched reports" },
  push_widget: { icon: IconChartBar, label: "Added widget" },
  // Capability tools (see dashboard_agent/tools/registry.py)
  draft_email: { icon: IconMail, label: "Drafted an email" },
  suggest_meeting_times: { icon: IconCalendarEvent, label: "Suggested meeting times" },
  list_data_sources: { icon: IconDatabase, label: "Listed data sources" },
  web_search: { icon: IconWorldSearch, label: "Searched the web" },
  // deepagents built-ins that are always live
  write_todos: { icon: IconChecklist, label: "Planned steps" },
  task: { icon: IconRobot, label: "Delegated to subagent" },
  eval: { icon: IconCode, label: "Ran code" },
  ls: { icon: IconFolder, label: "Listed files" },
  glob: { icon: IconFolder, label: "Found files" },
  grep: { icon: IconSearch, label: "Searched files" },
  read_file: { icon: IconFileText, label: "Read a file" },
  write_file: { icon: IconFilePencil, label: "Wrote a file" },
  edit_file: { icon: IconFilePencil, label: "Edited a file" },
};

export function toolMeta(name: string): { icon: TablerIcon; label: string } {
  return TOOL_META[name] || { icon: IconTool, label: name };
}

/**
 * The one-line summary shown on a tool chip's arg pill. For datasearch/query_sql
 * it's the `query` arg; otherwise a truncated JSON of the args. Matches app.js.
 */
export function chipArgSummary(name: string, args: Record<string, unknown>): string {
  // The code interpreter's arg IS a program — the raw JSON `{"code":"…"}` is
  // unreadable on a pill, so show the first non-empty line (usually a comment
  // stating intent). The full, highlighted code is revealed when the chip opens.
  if (name === "eval") {
    const code = String(args.code ?? "");
    return code.split("\n").map((l) => l.trim()).find(Boolean) || "";
  }
  // Show the single most meaningful arg per tool; fall back to truncated JSON.
  const primary: Record<string, string> = {
    datasearch: "query",
    web_search: "query",
    draft_email: "purpose",
    suggest_meeting_times: "purpose",
    list_data_sources: "area",
    read_file: "file_path",
    write_file: "file_path",
    edit_file: "file_path",
    glob: "pattern",
    grep: "pattern",
  };
  const key = primary[name];
  if (key) return String(args[key] || "");
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
