/**
 * One HTML artifact, rendered live in a sandboxed iframe and saveable as a PDF.
 *
 * SECURITY. The document is model-authored and may contain scripts, so the iframe gets
 * `allow-scripts` WITHOUT `allow-same-origin`. That pair is what gives the frame an
 * opaque origin: scripts run and external CDNs load, but the page cannot read this
 * app's `localStorage`, where the deployment token lives (see lib/config.ts). Adding
 * `allow-same-origin` alongside `allow-scripts` would hand a generated page the
 * ability to read that token, so the two must never appear together here.
 *
 * PDF via the browser's own print pipeline (`contentWindow.print()`), not a library.
 * The first attempt loaded html2pdf into the frame from a CDN and it failed there, but
 * that is only half the reason this is better: html2pdf is html2canvas plus jsPDF, so
 * it RASTERIZES. Its output is a picture of the document, with no selectable text and
 * soft edges, which is a poor thing to hand someone for a letter they mean to print.
 * `print()` produces real vector text, needs no network, and honours the document's own
 * `@media print` rules. The cost is a dialog: the browser asks where to save rather
 * than dropping a file into Downloads. `allow-modals` is what permits print() from
 * inside a sandboxed frame.
 */
import { useEffect, useImperativeHandle, useRef, useState } from "react";
import type { Ref } from "react";
import { artifactName, safeHtmlPrefix } from "@/lib/artifacts";

/**
 * Smallest gap between iframe reloads while content streams.
 *
 * A THROTTLE, not a debounce. It was a debounce, and that is why nothing appeared until
 * the write finished: token deltas land every few milliseconds, so each one cancelled
 * the pending timer and set a new one, and the trailing edge only ever arrived once the
 * stream stopped. A throttle guarantees a frame every interval however fast the input.
 */
const RENDER_INTERVAL_MS = 120;

/** What the tab bar can ask of the artifact it is showing. */
export interface HtmlArtifactHandle {
  /** Open the browser's print dialog on the document, for Save as PDF. */
  savePdf: () => void;
}

export interface HtmlArtifactProps {
  /** Absolute path on the agent's filesystem; also the tab key. */
  path: string;
  /** Content so far. Partial while `streaming`, canonical once the write completes. */
  content: string;
  /** True while write_file's args are still arriving. */
  streaming?: boolean;
  ref?: Ref<HtmlArtifactHandle>;
}

/** Live preview of one HTML artifact. */
export function HtmlArtifact({ path, content, streaming, ref }: HtmlArtifactProps) {
  const name = artifactName(path);
  // What the iframe is actually showing. Lags `content` by up to the interval.
  const [rendered, setRendered] = useState(() => safeHtmlPrefix(content));
  const frame = useRef<HTMLIFrameElement>(null);
  // Wall-clock time of the last iframe swap, so the throttle measures real elapsed time
  // rather than trusting a timer that keeps getting replaced.
  const lastRender = useRef(0);

  useEffect(() => {
    // A finished document renders at once: the throttle exists to pace the stream, and
    // making the user wait for the final frame would look like a stall.
    if (!streaming) {
      lastRender.current = Date.now();
      setRendered(safeHtmlPrefix(content));
      return;
    }
    const since = Date.now() - lastRender.current;
    // Leading edge. This branch is what makes progress inevitable: however fast content
    // arrives, once an interval of real time has passed the next change renders now
    // rather than being pushed further out.
    if (since >= RENDER_INTERVAL_MS) {
      lastRender.current = Date.now();
      setRendered(safeHtmlPrefix(content));
      return;
    }
    const t = window.setTimeout(() => {
      lastRender.current = Date.now();
      setRendered(safeHtmlPrefix(content));
    }, RENDER_INTERVAL_MS - since);
    return () => window.clearTimeout(t);
  }, [content, streaming]);

  useImperativeHandle(ref, () => ({
    savePdf: () => {
      // focus() first: printing a frame the user has not interacted with prints the top
      // document in some browsers, which would yield a PDF of the whole app instead.
      frame.current?.contentWindow?.focus();
      frame.current?.contentWindow?.print();
    },
  }));

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <iframe
        // Remounting per path (rather than reusing one frame) keeps two artifacts from
        // inheriting each other's scroll position and script state.
        key={path}
        ref={frame}
        title={name}
        srcDoc={rendered}
        // See the SECURITY note above: allow-same-origin must never be added here.
        // allow-modals is what lets the document print itself.
        sandbox="allow-scripts allow-popups allow-modals allow-forms"
        className="min-h-0 flex-1 border-0 bg-white"
      />
    </div>
  );
}
