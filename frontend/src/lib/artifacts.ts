/**
 * HTML artifacts: files the agent authors into `/workspace/artifacts/` and that the
 * dashboard shows as their own tab, rendering AS THEY STREAM.
 *
 * The streaming render is the reason this module exists. `write_file`'s `content`
 * argument arrives a token at a time, so what we hand the iframe is almost always a
 * truncated document. HTML tolerates that far better than JSON does: the HTML5 parsing
 * algorithm has defined recovery for unclosed elements, so a cut-off document renders
 * as far as it got. Three cases do NOT recover on their own, and `safeHtmlPrefix`
 * exists for exactly those:
 *
 *   1. a trailing half-written tag (`<div class="fo`) renders as stray text
 *   2. an unclosed `<style>` swallows the rest of the document as CSS
 *   3. an unclosed `<script>` swallows it as JS, and half a statement is a syntax error
 *
 * Nothing else needs fixing. Unclosed `<div>`/`<p>`/`<td>` are implied-closed at EOF by
 * the parser itself, so we deliberately do not track a tag stack.
 */

/** Directory the agent writes artifacts to. Anything here becomes a dashboard tab. */
export const ARTIFACT_DIR = "/workspace/artifacts/";

/** Whether `path` is an HTML artifact the dashboard should surface as a tab. */
export function isHtmlArtifactPath(path: string | undefined | null): boolean {
  if (!path) return false;
  return path.startsWith(ARTIFACT_DIR) && /\.html?$/i.test(path);
}

/** Tab label for an artifact: its basename, which is what the agent named it. */
export function artifactName(path: string): string {
  return path.slice(path.lastIndexOf("/") + 1) || path;
}

/** Raw-text elements: everything up to the matching close tag is text, not markup. */
const RAW_TEXT = ["script", "style"] as const;

/**
 * Index of the last `<tag ...>` opening that has no matching `</tag>` after it, plus
 * the index just past that opening tag's `>`. `null` when the element is not open.
 */
function openRawText(lower: string, tag: string): { start: number; bodyAt: number } | null {
  let start = -1;
  let from = 0;
  // Walk forwards so the LAST unclosed opening wins, and so `<scriptFoo` (a different
  // element whose name merely shares the prefix) is never mistaken for `<script`.
  for (;;) {
    const at = lower.indexOf(`<${tag}`, from);
    if (at < 0) break;
    const next = lower[at + tag.length + 1];
    if (next === undefined || next === ">" || next === " " || next === "\n" || next === "\t" || next === "/") {
      start = at;
    }
    from = at + 1;
  }
  if (start < 0) return null;
  if (lower.indexOf(`</${tag}`, start) >= 0) return null;
  const gt = lower.indexOf(">", start);
  // No `>` yet means the opening tag itself is still being written; the trailing-tag
  // trim handles that case, so report the body as empty.
  return { start, bodyAt: gt < 0 ? -1 : gt + 1 };
}

/**
 * The largest prefix of a partially-written HTML document that a browser renders the
 * way the finished document will start.
 *
 * Order matters. Raw-text repair runs BEFORE the trailing-tag trim, because a `<` in
 * JavaScript (`if (a < b)`) would otherwise look like a half-written tag and truncate
 * the document mid-script.
 */
export function safeHtmlPrefix(partial: string): string {
  if (!partial) return "";
  let s = partial;

  // A comment that has not closed hides everything after it. Drop the partial comment
  // rather than closing it: its content is invisible either way.
  const lastComment = s.lastIndexOf("<!--");
  if (lastComment >= 0 && s.indexOf("-->", lastComment) < 0) {
    s = s.slice(0, lastComment);
  }

  // An open `<script>`/`<style>`. Whichever opened later is the one we are inside.
  let open: { tag: string; start: number; bodyAt: number } | null = null;
  for (const tag of RAW_TEXT) {
    const found = openRawText(s.toLowerCase(), tag);
    if (found && (open === null || found.start > open.start)) {
      open = { tag, ...found };
    }
  }
  if (open !== null && open.bodyAt >= 0) {
    if (open.tag === "script") {
      // Half a statement throws, and a throw stops every later script on the page. An
      // empty script is inert, and the real one runs on the frame after it completes.
      s = `${s.slice(0, open.bodyAt)}</script>`;
    } else {
      // CSS parsing is itself error-tolerant: a trailing incomplete rule is dropped and
      // the rules already written still apply, which is what makes the page build up
      // its styling visibly rather than restyling all at once at the end.
      s = `${s}</style>`;
    }
    return s;
  }

  // A trailing `<` with no `>` after it is a tag mid-write. Cut it, or the raw source
  // text of a half-written tag flashes on screen as content.
  const lastLt = s.lastIndexOf("<");
  if (lastLt > s.lastIndexOf(">")) s = s.slice(0, lastLt);
  return s;
}
