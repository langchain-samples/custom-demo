/**
 * One vocabulary for the agent's tools, in both tenses.
 *
 * `done` is what a finished tool chip says in the chat log ("Searched reports"); `active` is
 * what a live status line says while it runs ("Searching reports"). They lived apart before:
 * the chat had its own past-tense map and the voice shell had a separate set of conversational
 * phrases, so the orb and the chat log described the same tool call in different words - and
 * the voice map covered ten tools to the chat's seventeen, so the rest fell back to a shrug.
 *
 * Deliberately in `lib/` with NO icon imports, so it can be shared by the chat helpers (which
 * add the icons) and by voice.ts (which Node type-strips in voice_test.js and cannot resolve
 * `@/` aliases or JSX).
 *
 * Past tense is not mechanically derivable from the present ("Read a file", "Found files"),
 * so both are written out.
 */
export interface ToolLabel {
  /** Past tense, for a completed chip. */
  done: string;
  /** Present participle, for a live status line. */
  active: string;
}

export const TOOL_LABELS: Record<string, ToolLabel> = {
  // Core
  datasearch: { done: "Searched reports", active: "Searching reports" },
  push_widget: { done: "Added widget", active: "Building the dashboard" },
  // Capability tools (see dashboard_agent/tools/registry.py)
  draft_email: { done: "Drafted an email", active: "Drafting an email" },
  suggest_meeting_times: { done: "Suggested meeting times", active: "Finding meeting times" },
  list_data_sources: { done: "Listed data sources", active: "Listing data sources" },
  web_search: { done: "Searched the web", active: "Searching the web" },
  ask_user: { done: "Asked a question", active: "Asking you a question" },
  // deepagents built-ins that are always live
  write_todos: { done: "Planned steps", active: "Planning the steps" },
  task: { done: "Delegated to subagent", active: "Delegating to a subagent" },
  eval: { done: "Ran code", active: "Running code" },
  execute: { done: "Ran command", active: "Running a command" },
  ls: { done: "Listed files", active: "Listing files" },
  glob: { done: "Found files", active: "Finding files" },
  grep: { done: "Searched files", active: "Searching files" },
  read_file: { done: "Read a file", active: "Reading a file" },
  write_file: { done: "Wrote a file", active: "Writing a file" },
  edit_file: { done: "Edited a file", active: "Editing a file" },
};

/** The live label for a tool, or a readable fallback for one we do not know. */
export function activeLabel(name: string): string {
  return TOOL_LABELS[name]?.active || "Working on it";
}
