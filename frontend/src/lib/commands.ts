/**
 * Composer slash commands: the table the palette offers, and the parser behind
 * `/goal`.
 *
 * Lives in lib/ rather than in ChatPanel so it is pure (Node-testable via type
 * stripping, like streamEvent.ts) and so the component file exports only
 * components.
 */

/** Slash commands the composer recognises and completes. */
export const COMMANDS = [
  {
    name: "/goal",
    hint: 'say what "done" looks like, and every answer is graded against it until it is met',
  },
] as const;

/** What a typed `/goal …` asked for. */
export type GoalCommand =
  | { kind: "show" }
  | { kind: "clear" }
  | { kind: "set"; text: string };

/**
 * Read a `/goal` command out of what the user typed, or null if it isn't one.
 *
 * Anchored at the start, then a fallback for `/goal` used mid-sentence ("set a
 * /goal to build me a dashboard"), which is how people actually type it and which
 * would otherwise sail past as an ordinary question.
 */
export function parseGoalCommand(raw: string): GoalCommand | null {
  const m = /^\/goal\b\s*([\s\S]*)$/i.exec(raw.trim()) || /\/goal\b\s*([\s\S]*)$/i.exec(raw);
  if (!m) return null;
  const rest = m[1].trim();
  if (!rest || /^(show|status)$/i.test(rest)) return { kind: "show" };
  if (/^clear$/i.test(rest)) return { kind: "clear" };
  return { kind: "set", text: rest };
}
