/**
 * LangSmith trace-project naming.
 *
 * Convention: the trace project is the customer's name. Kept in one place so the
 * SPA and `dashboard_agent/assistant_setup.py` (which writes the same name onto
 * new assistants) stay in sync. An explicit `ls_project` on the assistant's
 * context always wins, letting a DE point a demo at an existing project.
 */
import type { Assistant } from "./api";

/** Tracing project for an assistant's runs: the customer name (context wins). */
export function traceProject(a: Assistant | null, id = ""): string {
  const stored = a?.context?.ls_project;
  if (typeof stored === "string" && stored.trim()) return stored.trim();
  return (a?.metadata?.customer || a?.name || id || "").trim() || "customer";
}
