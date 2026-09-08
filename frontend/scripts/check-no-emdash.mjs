#!/usr/bin/env node
/**
 * Fails if an em-dash (—, U+2014) appears in anything a person reads.
 *
 * Two scopes, because the rule is the same but what counts as prose is not:
 *
 *   frontend/src/**\/*.ts{,x}   code comments are EXEMPT (explanatory prose for
 *                              whoever maintains the file is not user-facing),
 *                              so comments are stripped before the scan.
 *   README.md, AGENTS.md,      every character counts: these files are prose
 *   docs/, evals/*.md          from top to bottom.
 *
 * The docs half was added after a review found 170 em-dashes sitting in them,
 * untouched, because the scan had only ever covered the ts/tsx tree, so the one
 * rule the repo bothers to enforce mechanically was not enforced where most of
 * the writing actually lives.
 *
 * Mirrors the agent-side rule (prompt.py + the demo-brief tests): no em-dashes
 * in anything a user reads. Use commas, colons, parentheses, or " - " instead.
 *
 * Run via `npm run check:emdash` (wired into the CI lint step).
 */
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs"
import { join, relative } from "node:path"

const REPO = new URL("../..", import.meta.url).pathname
const EM_DASH = "—"

/** Directories never worth walking. */
const SKIP = new Set(["node_modules", ".git", ".venv", "dist", "__pycache__", ".langgraph_api"])

/**
 * What to scan, and whether comments are exempt there.
 *
 * `code: true` means the file is source, so comments are stripped first. Prose
 * files are scanned whole.
 */
const TARGETS = [
  { path: "frontend/src", match: /\.tsx?$/, code: true },
  { path: "README.md", match: /\.md$/, code: false },
  { path: "AGENTS.md", match: /\.md$/, code: false },
  { path: "docs", match: /\.(md|html)$/, code: false },
  { path: "evals", match: /\.md$/, code: false },
]

/** Remove block and line comments so only real code/strings remain. */
function stripComments(src) {
  // Block comments (incl. JSDoc). Non-greedy, spans lines.
  let out = src.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, " "))
  // Line comments, but not inside a string/URL like https:// (best-effort:
  // only strip when the // is preceded by start-of-line or whitespace and is
  // not part of `:ived//`). Keep it simple: strip from a `//` that has a space
  // or line-start before it.
  out = out.replace(/(^|[^:"'`\\])\/\/[^\n]*/g, (m, p1) => p1 + " ".repeat(m.length - p1.length))
  return out
}

function walk(dir, match, acc = []) {
  for (const name of readdirSync(dir)) {
    if (SKIP.has(name)) continue
    const p = join(dir, name)
    if (statSync(p).isDirectory()) walk(p, match, acc)
    else if (match.test(name)) acc.push(p)
  }
  return acc
}

/** Every file a target covers, whether it names a file or a directory. */
function filesFor({ path, match }) {
  const abs = join(REPO, path)
  if (!existsSync(abs)) return []
  return statSync(abs).isDirectory() ? walk(abs, match) : [abs]
}

const offenders = []
for (const target of TARGETS) {
  for (const file of filesFor(target)) {
    const raw = readFileSync(file, "utf8")
    const scanned = target.code ? stripComments(raw) : raw
    const rawLines = raw.split("\n")
    scanned.split("\n").forEach((line, i) => {
      if (line.includes(EM_DASH)) {
        offenders.push(`${relative(REPO, file)}:${i + 1}: ${rawLines[i].trim().slice(0, 110)}`)
      }
    })
  }
}

if (offenders.length) {
  console.error(`\n✖ em-dash (—) found in ${offenders.length} location(s) a person reads:\n`)
  for (const o of offenders) console.error("  " + o)
  console.error(`\nUse commas, colons, parentheses, or " - " instead. (Code comments are exempt.)\n`)
  process.exit(1)
}
console.log("✓ no em-dashes in prose or user-facing code")
