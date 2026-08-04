#!/usr/bin/env node
/**
 * Fails if an em-dash (—, U+2014) appears in user-facing frontend code — i.e.
 * anywhere in the src ts/tsx tree OUTSIDE of comments. Code comments are
 * stripped (JSDoc/block + line), so explanatory prose in comments is allowed;
 * rendered JSX text and string literals (labels, placeholders, tooltips,
 * alerts) are not.
 *
 * Mirrors the agent-side rule (prompt.py + the demo-brief tests): no em-dashes in
 * anything a user reads. Use commas, colons, parentheses, or " - " instead.
 *
 * Run via `npm run check:emdash` (wired into the CI lint step).
 */
import { readdirSync, readFileSync, statSync } from "node:fs"
import { join } from "node:path"

const ROOT = new URL("../src", import.meta.url).pathname
const EM_DASH = "—"

/** Remove block and line comments so only real code/strings remain. */
function stripComments(src) {
  // Block comments (incl. JSDoc). Non-greedy, spans lines.
  let out = src.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, " "))
  // Line comments — but not inside a string/URL like https:// (best-effort:
  // only strip when the // is preceded by start-of-line or whitespace and is
  // not part of `:ived//`). Keep it simple: strip from a `//` that has a space
  // or line-start before it.
  out = out.replace(/(^|[^:"'`\\])\/\/[^\n]*/g, (m, p1) => p1 + " ".repeat(m.length - p1.length))
  return out
}

function walk(dir, acc = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    const st = statSync(p)
    if (st.isDirectory()) walk(p, acc)
    else if (/\.tsx?$/.test(name)) acc.push(p)
  }
  return acc
}

const offenders = []
for (const file of walk(ROOT)) {
  const raw = readFileSync(file, "utf8")
  const code = stripComments(raw)
  const rawLines = raw.split("\n")
  code.split("\n").forEach((line, i) => {
    if (line.includes(EM_DASH)) {
      offenders.push(`${file.replace(ROOT, "src")}:${i + 1}: ${rawLines[i].trim()}`)
    }
  })
}

if (offenders.length) {
  console.error(`\n✖ em-dash (—) found in ${offenders.length} user-facing location(s):\n`)
  for (const o of offenders) console.error("  " + o)
  console.error(`\nUse commas, colons, parentheses, or " - " instead. (Comments are exempt.)\n`)
  process.exit(1)
}
console.log("✓ no em-dashes in user-facing frontend code")
