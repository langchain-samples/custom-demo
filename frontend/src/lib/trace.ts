/**
 * LangSmith trace-project naming.
 *
 * Kept out of the settings component so it can be unit-tested, and so the
 * convention lives in one place — `dashboard_agent/assistant_setup.py` writes
 * the same name onto new assistants.
 */
import type { Assistant } from "./api";

export const TRACE_SUFFIX = "corebot-demo";

/** Slugify a client name the way the backend's `slugify()` does. */
export function slugifyClient(name: string): string {
  return (name || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/**
 * Tracing project for an assistant's runs: `<client>-corebot-demo`.
 *
 * Suffixed so demo traces are obvious among whatever else lives in the
 * workspace, and so they can never collide with a real project that happens to
 * share the customer's name. An explicit `ls_project` on the assistant's context
 * wins, letting a DE point a demo at an existing project.
 */
export function traceProject(a: Assistant | null, id = ""): string {
  const stored = a?.context?.ls_project;
  if (typeof stored === "string" && stored.trim()) return stored.trim();
  const client = (a?.metadata?.customer || a?.name || id || "").trim();
  return `${slugifyClient(client) || "customer"}-${TRACE_SUFFIX}`;
}
