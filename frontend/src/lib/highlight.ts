/**
 * Syntax highlighting for tool-chip code blocks (the `eval` script / `execute`
 * command). Uses Shiki directly with a single dark theme so token colors are
 * concrete inline styles — deterministic, unlike Streamdown's dual-theme CSS-var
 * path which renders monochrome in our setup.
 *
 * It also understands shell heredocs: `python3 << 'EOF' … EOF` is a bash wrapper
 * around a Python body, so the body is highlighted as Python (where the value is)
 * rather than as undifferentiated shell.
 */
// Fine-grained core + JS regex engine, importing ONLY the grammars/theme we use.
// (The full `shiki` bundle would emit a lazy chunk for every bundled language —
// hundreds of unused files in the build output.)
import { createHighlighterCore, type HighlighterCore } from "shiki/core";
import { createJavaScriptRegexEngine } from "shiki/engine/javascript";

const THEME = "github-dark";
const KNOWN = new Set<string>(["bash", "python", "javascript", "json"]);
const ALIAS: Record<string, string> = {
  js: "javascript",
  ts: "typescript",
  sh: "bash",
  shell: "bash",
  py: "python",
};

let highlighterPromise: Promise<HighlighterCore> | null = null;

function getHighlighter(): Promise<HighlighterCore> {
  if (!highlighterPromise) {
    highlighterPromise = createHighlighterCore({
      themes: [import("shiki/themes/github-dark.mjs")],
      langs: [
        import("shiki/langs/bash.mjs"),
        import("shiki/langs/python.mjs"),
        import("shiki/langs/javascript.mjs"),
        import("shiki/langs/json.mjs"),
      ],
      engine: createJavaScriptRegexEngine(),
    });
  }
  return highlighterPromise;
}

/** Map an incoming fence language to one we bundled; unknown → plaintext ("txt"). */
function resolveLang(lang: string): string {
  const l = ALIAS[lang] || lang;
  return KNOWN.has(l) ? l : "txt";
}

/** The inner `<code>…</code>` of a Shiki `<pre>` — the highlighted line spans. */
function innerCode(html: string): string {
  const m = html.match(/<code[^>]*>([\s\S]*)<\/code>/);
  return m ? m[1] : html;
}

export interface CodeSegment {
  code: string;
  lang: string;
}

/**
 * Split a command into highlightable segments. A `bash` command containing a
 * heredoc (`<<[-] ['"]?DELIM['"]?` … `DELIM`) becomes: the wrapper (bash), the
 * heredoc body (the interpreter's language), and the closing delimiter (bash).
 * Everything else is a single segment in its own language.
 */
export function toSegments(code: string, lang: string): CodeSegment[] {
  const single: CodeSegment[] = [{ code, lang }];
  if (lang !== "bash") return single;

  const opener = code.match(/<<-?\s*(['"]?)([A-Za-z_][A-Za-z0-9_]*)\1/);
  if (!opener) return single;
  const delim = opener[2];

  const lines = code.split("\n");
  const openIdx = lines.findIndex((l) => l.includes(opener[0]));
  if (openIdx < 0) return single;
  let closeIdx = -1;
  for (let i = openIdx + 1; i < lines.length; i++) {
    // `<<-` allows a tab-indented terminator; a plain `<<` requires it at column 0.
    if (lines[i].trim() === delim) {
      closeIdx = i;
      break;
    }
  }
  if (closeIdx < 0) return single; // body still streaming / unterminated

  const bodyLang = /\bpython3?\b/.test(lines[openIdx])
    ? "python"
    : /\bnode\b/.test(lines[openIdx])
      ? "javascript"
      : "bash";

  const segs: CodeSegment[] = [{ code: lines.slice(0, openIdx + 1).join("\n"), lang: "bash" }];
  const body = lines.slice(openIdx + 1, closeIdx).join("\n");
  if (body) segs.push({ code: body, lang: bodyLang });
  const tail = lines.slice(closeIdx).join("\n");
  if (tail) segs.push({ code: tail, lang: "bash" });
  return segs;
}

/**
 * Highlight `code` (splitting heredocs per `toSegments`) into the inner HTML of a
 * single `<code>` element — token spans carrying inline colors, segments joined by
 * newlines. Throws only if Shiki itself fails to load; callers fall back to plain.
 */
export async function highlightSegments(code: string, lang: string): Promise<string> {
  const hl = await getHighlighter();
  const parts = toSegments(code, lang).map((s) =>
    innerCode(hl.codeToHtml(s.code, { lang: resolveLang(s.lang), theme: THEME })),
  );
  return parts.join("\n");
}
