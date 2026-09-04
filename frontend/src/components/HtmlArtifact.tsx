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
 * PDF via the browser's own print pipeline, not a library.
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
import type { ReactNode, Ref } from "react";
import { ReasoningText } from "@/components/agents/loading-states/reasoning-text";
import {
  artifactName,
  hasRenderableBody,
  safeHtmlPrefix,
  skeletonReveal,
} from "@/lib/artifacts";

/**
 * Smallest gap between iframe reloads while content streams.
 *
 * A THROTTLE, not a debounce. It was a debounce, and that is why nothing appeared until
 * the write finished: token deltas land every few milliseconds, so each one cancelled
 * the pending timer and set a new one, and the trailing edge only ever arrived once the
 * stream stopped. A throttle guarantees a frame every interval however fast the input.
 */
const RENDER_INTERVAL_MS = 120;

/** Message that asks the document to print itself. */
const ASK_PRINT = "da-artifact-print";

/**
 * Appended to every rendered document so it can print ITSELF.
 *
 * The parent cannot do it. `contentWindow` is cross-origin here by design (no
 * allow-same-origin), and the cross-origin allowlist is only postMessage / focus /
 * blur / close / location and friends - `print()` is not on it, so calling it from
 * outside throws a SecurityError and nothing happens. Asking the document to print
 * itself is the one route that works, and `allow-modals` is what permits it.
 */
const PRINT_BOOTSTRAP = `<script>
window.addEventListener("message", function (e) {
  if ((e.data || {}).type === ${JSON.stringify(ASK_PRINT)}) window.print();
});
</script>`;

/**
 * Shown while the document has nothing paintable yet.
 *
 * The agent writes `<head>` first and its `<style>` block is a third of the file, so the
 * first ~30 seconds of a write are genuinely blank (see hasRenderableBody). The work is
 * real, so say what it is rather than showing an empty white pane.
 */
const BUILDING_PHRASES = [
  "Building the artifact",
  "Laying out the document",
  "Writing styles",
  "Placing the content",
];

/** What the tab bar can ask of the artifact it is showing. */
export interface HtmlArtifactHandle {
  /** Open the browser's print dialog on the document, for Save as PDF. */
  savePdf: () => void;
}

/** One grey bar. `w` is a Tailwind width class so the rows can be ragged. */
function Bar({ w, h = "h-3" }: { w: string; h?: string }) {
  return <div className={`rounded bg-neutral-200/90 ${h} ${w}`} />;
}

/**
 * The skeleton's rows, in the order a document is written.
 *
 * A flat list rather than nested JSX so the reveal below is a simple slice: how many
 * rows are showing is then one number, driven by bytes written, instead of a spread of
 * per-section conditionals.
 */
const SKELETON_ROWS: { el: ReactNode; key: string }[] = [
  { key: "band", el: (
    <div className="flex flex-col gap-3 rounded-xl bg-neutral-100 p-6">
      <Bar w="w-2/5" h="h-2" />
      <Bar w="w-3/4" h="h-6" />
      <Bar w="w-1/2" h="h-2.5" />
    </div>
  ) },
  { key: "h1", el: <Bar w="w-1/4" h="h-4" /> },
  { key: "p1", el: <Bar w="w-full" /> },
  { key: "p2", el: <Bar w="w-11/12" /> },
  { key: "p3", el: <Bar w="w-4/5" /> },
  { key: "cards", el: (
    <div className="grid grid-cols-4 gap-3">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="flex flex-col gap-2 rounded-lg border border-neutral-200 p-4">
          <Bar w="w-2/3" h="h-2" />
          <Bar w="w-1/2" h="h-5" />
        </div>
      ))}
    </div>
  ) },
  { key: "h2", el: <Bar w="w-1/3" h="h-4" /> },
  { key: "p4", el: <Bar w="w-full" /> },
  { key: "p5", el: <Bar w="w-10/12" /> },
];

/**
 * A placeholder shaped like the documents this agent writes: title band, meta line,
 * summary paragraph, a row of stat cards, then sections.
 *
 * Deliberately NOT the generic avatar-and-image card skeleton. A skeleton is a claim
 * about the layout that is coming, so a circle where no artifact has an avatar is worse
 * than no skeleton at all - it resolves into something structurally unrelated. Every
 * brief produced so far has these same bones, which is what makes this one honest.
 *
 * It GROWS while the document is being written, on a clock (see skeletonReveal). Bytes
 * were the first attempt and they have one hole: the tab appears as soon as write_file's
 * path argument is known, which is before its content argument starts streaming, so a
 * byte-driven skeleton sat at one row through exactly the gap where it most needs to
 * look alive.
 *
 * aria-hidden: it carries no information. The rotating status text underneath is what
 * a screen reader should hear.
 */
function ArtifactSkeleton({ reveal }: { reveal: number }) {
  // At least one row from the start, so the pane is never blank.
  const shown = Math.max(1, Math.round(reveal * SKELETON_ROWS.length));
  return (
    <div aria-hidden className="flex flex-col gap-6 p-8">
      {SKELETON_ROWS.slice(0, shown).map(({ key, el }, i) => (
        <div
          key={key}
          // Only the newest row pulses: pulsing everything made the whole pane throb and
          // hid the fact that rows were arriving at all.
          className={i === shown - 1 ? "animate-pulse" : ""}
        >
          {el}
        </div>
      ))}
    </div>
  );
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
      const target = frame.current?.contentWindow;
      if (!target) return;
      // focus() first (it IS allowed cross-origin) so the print dialog belongs to the
      // artifact, then ask the document to print itself.
      target.focus();
      target.postMessage({ type: ASK_PRINT }, "*");
    },
  }));

  // Blank-but-busy: a document that has begun but has no visible content yet. Once
  // anything paintable arrives the iframe takes over and never reverts, so a slow
  // stretch mid-document does not flip back to the loader.
  const building = !!streaming && !hasRenderableBody(rendered);

  /**
   * A clock for the skeleton's growth, running only while it is on screen.
   *
   * The start time is a ref rather than state so beginning to build does not itself
   * cause a render, and it is cleared when building ends so a second artifact in the
   * same tab starts from zero rather than inheriting the first one's age.
   */
  const buildStartedAt = useRef<number | null>(null);
  const [buildElapsed, setBuildElapsed] = useState(0);
  useEffect(() => {
    if (!building) {
      buildStartedAt.current = null;
      setBuildElapsed(0);
      return;
    }
    buildStartedAt.current ??= Date.now();
    const tick = () => setBuildElapsed(Date.now() - (buildStartedAt.current ?? Date.now()));
    tick();
    // A row lands roughly every three seconds now, so a quarter-second tick is already
    // far finer than anything visible.
    const id = window.setInterval(tick, 250);
    return () => window.clearInterval(id);
  }, [building]);

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <iframe
        // Remounting per path (rather than reusing one frame) keeps two artifacts from
        // inheriting each other's scroll position and script state.
        key={path}
        ref={frame}
        title={name}
        srcDoc={rendered + PRINT_BOOTSTRAP}
        // See the SECURITY note above: allow-same-origin must never be added here.
        // allow-modals is what lets the document print itself.
        sandbox="allow-scripts allow-popups allow-modals allow-forms"
        // ALWAYS laid out, never display:none. Hiding it was a real bug: an iframe that
        // is display:none when srcDoc is assigned does not load that document, and when
        // it becomes visible again srcDoc has not CHANGED since the last commit, so
        // nothing triggers a load and the pane stays blank. Switching tabs appeared to
        // fix it only because that remounts the frame.
        className="min-h-0 flex-1 border-0 bg-white"
      />
      {building && (
        // An overlay rather than a swap. The frame underneath is genuinely empty at this
        // point, so covering it looks identical to replacing it, and the iframe keeps
        // laying out and loading throughout.
        //
        // On WHITE, matching the iframe, so handing over to the real document is not
        // also a change of background colour.
        <div className="absolute inset-0 overflow-hidden bg-white">
          <ArtifactSkeleton reveal={skeletonReveal(buildElapsed)} />
          {/* The skeleton says content is coming; only this says why the wait is long
              enough to notice, and it is the line a presenter talks over. */}
          <div className="absolute inset-x-0 bottom-8 flex justify-center">
            <ReasoningText phrases={BUILDING_PHRASES} />
          </div>
        </div>
      )}
    </div>
  );
}
