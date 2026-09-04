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

/**
 * Whether a partial document has anything a viewer could actually SEE yet.
 *
 * An agent writes a full `<head>` first, and its `<style>` block is a big share of the
 * document: measured on real artifacts, `<body>` began 4301 bytes into 12558 (34%) and
 * 4061 into 17312 (23%). Until it starts there is nothing to paint, so the iframe was
 * correctly showing a blank page for the first ~30 seconds of a write, which reads as
 * broken rather than busy.
 *
 * Head content is not the only thing that renders to nothing: an opening `<body>` whose
 * children have not arrived, or a `<div>` with no text in it yet, are equally empty. So
 * this asks the real question - is there any non-whitespace text after `<body>` - rather
 * than looking for the tag alone.
 */
export function hasRenderableBody(partial: string): boolean {
  if (!partial) return false;
  const lower = partial.toLowerCase();
  const bodyAt = lower.indexOf("<body");
  let rest: string;
  if (bodyAt >= 0) {
    const gt = partial.indexOf(">", bodyAt);
    if (gt < 0) return false; // the <body> tag itself is still being written
    rest = partial.slice(gt + 1);
  } else if (lower.includes("<html") || lower.includes("<head")) {
    // A document that has declared itself but not reached its body.
    return false;
  } else {
    // A fragment with no <html>/<head> at all: whatever is there is the content.
    rest = partial;
  }
  // Drop raw-text elements whole (their contents are CSS/JS, not visible text), then
  // every remaining tag, and see if any real characters are left.
  const text = rest
    .replace(/<(script|style)\b[\s\S]*?(<\/\1>|$)/gi, "")
    .replace(/<[^>]*>/g, "")
    .replace(/&nbsp;/gi, " ")
    .trim();
  if (text.length > 0) return true;
  // No text yet, but a self-contained visual (an img/svg/canvas) counts as something.
  return /<(img|svg|canvas|video|table)\b/i.test(rest);
}

/** How long the skeleton takes to fill. Longer than most writes, deliberately: the
 *  point is that it is still moving when the document lands, not that it finishes. */
const REVEAL_MS = 30_000;

/**
 * How much of the skeleton is showing, 0..1, from how long the write has been running.
 *
 * EASED, not linear, and that is the whole substance of this function. Linear over 30s
 * put the first row change at five seconds (round(t/30*9) only reaches 2 at t=5), and
 * five motionless seconds is exactly how long someone looks before deciding a loader is
 * broken - which is what was reported, three times. It was growing; nobody could tell.
 *
 * sqrt front-loads it: rows at roughly 0.8s, 2.3s, 4.5s, 7.5s, then slowing to 27s for
 * the last one. Still 1 -> 9 across thirty seconds, so the brief is unchanged, but the
 * early seconds - the only ones a fast write ever reaches - now visibly move. It also
 * matches how these documents are really written: the shell appears almost at once and
 * the detail accretes, so the curve is honest about what is coming rather than pacing
 * evenly through a process that is not even.
 */
export function skeletonReveal(elapsedMs: number): number {
  if (!Number.isFinite(elapsedMs) || elapsedMs <= 0) return 0;
  return Math.min(1, Math.sqrt(elapsedMs / REVEAL_MS));
}

/**
 * Artifact paths to drop when `path` is registered, because they are half-streamed
 * versions of it rather than files of their own.
 *
 * write_file's arguments arrive a character at a time, and the tab opens as soon as
 * file_path looks like an artifact, so the pane fills while the document is being
 * written rather than after. The cost is that ".../report.htm" is a valid-looking
 * artifact path one frame before ".../report.html" is, so a single write opened two
 * tabs: the real one, and a phantom that no tool result ever matched and that therefore
 * span forever. Both truncate to the same label in the tab bar, so it read as the same
 * file listed twice.
 *
 * A strict prefix is the exact signature of that: a real path is never a prefix of
 * another real path AND still streaming. Finished artifacts are left alone, so a genuine
 * pair like report.htm and report.html both survive once their writes complete.
 */
export function provisionalDuplicates(
  paths: Iterable<string>,
  path: string,
  isStreaming: (p: string) => boolean,
): string[] {
  const out: string[] = [];
  for (const p of paths) {
    if (p !== path && path.startsWith(p) && isStreaming(p)) out.push(p);
  }
  return out;
}
