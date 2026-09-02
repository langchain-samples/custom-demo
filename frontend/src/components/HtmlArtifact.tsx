/**
 * One HTML artifact, rendered live in a sandboxed iframe, downloadable as a PDF.
 *
 *   ┌─ report.html ─────────────────────── ⬇ ┐
 *   │  <iframe srcDoc=... sandbox=...>        │
 *   └─────────────────────────────────────────┘
 *
 * SECURITY. The document is model-authored and may contain scripts, so the iframe gets
 * `allow-scripts` WITHOUT `allow-same-origin`. That pair is what gives the frame an
 * opaque origin: scripts run and external CDNs load, but the page cannot read this
 * app's `localStorage`, where the deployment token lives (see lib/config.ts). Adding
 * `allow-same-origin` alongside `allow-scripts` would hand a generated page the
 * ability to read that token, so the two must never appear together here.
 *
 * That constraint is also why the PDF is built INSIDE the frame. html2pdf works on a
 * live DOM, and DashboardCanvas can hand it React's own trusted output; here the only
 * way to do the same in the parent document would be to inject this HTML into it,
 * which defeats the sandbox (an `<img onerror=...>` fires even when inserted through
 * innerHTML). So the parent asks over postMessage, and a bootstrap script appended to
 * the document does the work in the frame's own origin.
 *
 * The `srcDoc` swap is debounced. `write_file`'s content argument streams a token at a
 * time, and reassigning `srcDoc` reloads the frame, so a swap per token would restart
 * the document (and re-run its scripts) tens of times a second.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { IconDownload } from "@tabler/icons-react";
import { Button } from "@/components/ui/button";
import { artifactName, safeHtmlPrefix } from "@/lib/artifacts";

/** Frames per second cap on reloading the iframe while content streams. */
const RENDER_DEBOUNCE_MS = 120;

/** How long to wait for the frame to produce a PDF before offering the HTML instead. */
const PDF_TIMEOUT_MS = 30_000;

/** Message names exchanged with the bootstrap script. Namespaced to avoid collisions. */
const ASK_PDF = "da-artifact-pdf";
const PDF_DONE = "da-artifact-pdf-done";

/**
 * Appended to every rendered document. Inert until the parent asks for a PDF, then
 * pulls html2pdf from the CDN and saves the page. `scale: 2` because html2canvas
 * rasterizes: at 1 the text is visibly soft.
 *
 * It reports back either way, so a blocked CDN surfaces as a fallback rather than a
 * button that silently does nothing.
 */
const PDF_BOOTSTRAP = `<script>
window.addEventListener("message", function (e) {
  var d = e.data || {};
  if (d.type !== ${JSON.stringify(ASK_PDF)}) return;
  var reply = function (ok, error) {
    parent.postMessage({ type: ${JSON.stringify(PDF_DONE)}, ok: ok, error: error }, "*");
  };
  var run = function () {
    try {
      window
        .html2pdf()
        .set({
          margin: 10,
          filename: d.filename,
          image: { type: "jpeg", quality: 0.98 },
          html2canvas: { scale: 2, useCORS: true },
          jsPDF: { unit: "mm", format: "a4", orientation: "portrait" },
        })
        .from(document.body)
        .save()
        .then(function () { reply(true); }, function (err) { reply(false, String(err)); });
    } catch (err) {
      reply(false, String(err));
    }
  };
  if (window.html2pdf) return run();
  var s = document.createElement("script");
  // Pinned, and to the same version as the bundled copy DashboardCanvas uses, so
  // the two exports do not drift apart.
  s.src = "https://cdnjs.cloudflare.com/ajax/libs/html2pdf.js/0.14.0/html2pdf.bundle.min.js";
  s.onload = run;
  s.onerror = function () { reply(false, "could not load the PDF library"); };
  document.head.appendChild(s);
});
</script>`;

export interface HtmlArtifactProps {
  /** Absolute path on the agent's filesystem; also the tab key. */
  path: string;
  /** Content so far. Partial while `streaming`, canonical once the write completes. */
  content: string;
  /** True while write_file's args are still arriving. */
  streaming?: boolean;
}

/** Live preview of one HTML artifact, with a PDF download. */
export function HtmlArtifact({ path, content, streaming }: HtmlArtifactProps) {
  const name = artifactName(path);
  const pdfName = name.replace(/\.html?$/i, "") + ".pdf";
  // What the iframe is actually showing. Lags `content` by up to the debounce.
  const [rendered, setRendered] = useState(() => safeHtmlPrefix(content));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const frame = useRef<HTMLIFrameElement>(null);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    // A finished document renders immediately: the debounce exists to throttle the
    // stream, and making the user wait for the final frame would look like a stall.
    if (!streaming) {
      setRendered(safeHtmlPrefix(content));
      return;
    }
    timer.current = window.setTimeout(
      () => setRendered(safeHtmlPrefix(content)),
      RENDER_DEBOUNCE_MS,
    );
    return () => window.clearTimeout(timer.current);
  }, [content, streaming]);

  /** Save the raw document. The fallback when the frame cannot make a PDF. */
  const downloadHtml = useCallback(() => {
    const blob = new Blob([content], { type: "text/html;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
  }, [content, name]);

  useEffect(() => {
    if (!saving) return;
    const onReply = (e: MessageEvent) => {
      // The frame is opaque-origin, so there is no origin to check against. Safe
      // because this only ever reads a boolean and a string for a local message name,
      // and acts on neither beyond clearing a spinner.
      if ((e.data || {}).type !== PDF_DONE) return;
      setSaving(false);
      if (!e.data.ok) {
        setError("Could not build the PDF. Saved the HTML instead.");
        downloadHtml();
      }
    };
    window.addEventListener("message", onReply);
    const bail = window.setTimeout(() => {
      setSaving(false);
      setError("The PDF timed out. Saved the HTML instead.");
      downloadHtml();
    }, PDF_TIMEOUT_MS);
    return () => {
      window.removeEventListener("message", onReply);
      window.clearTimeout(bail);
    };
  }, [saving, downloadHtml]);

  const download = () => {
    setError("");
    const target = frame.current?.contentWindow;
    if (!target) {
      downloadHtml();
      return;
    }
    setSaving(true);
    // "*" as the target origin: an opaque-origin frame cannot be addressed by name.
    // Only a filename goes over this channel, never anything sensitive.
    target.postMessage({ type: ASK_PDF, filename: pdfName }, "*");
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* No path label: the tab above already names the file, and repeating the whole
          /workspace/artifacts/... path cost a line of height on every artifact. The tab
          carries it as a tooltip for when it is actually wanted. */}
      <div className="flex items-center gap-2 px-4 py-1">
        {streaming && (
          <span className="flex-shrink-0 rounded bg-panel-2 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
            Writing
          </span>
        )}
        <Button
          variant="ghost"
          size="sm"
          onClick={download}
          // Mid-write the document is incomplete, so a PDF of it would be too.
          disabled={!content || streaming || saving}
          className="ml-auto flex-shrink-0"
          title={`Download ${pdfName}`}
        >
          <IconDownload className="size-4" />
          {saving ? "Building PDF…" : "Download PDF"}
        </Button>
      </div>
      {error && (
        <p className="m-0 px-4 pb-1 text-[11px] leading-snug text-muted-foreground">{error}</p>
      )}
      <iframe
        // Remounting per path (rather than reusing one frame) keeps two artifacts from
        // inheriting each other's scroll position and script state.
        key={path}
        ref={frame}
        title={name}
        srcDoc={rendered + PDF_BOOTSTRAP}
        // See the SECURITY note above: allow-same-origin must never be added here.
        // allow-downloads is what lets the in-frame PDF save reach the user.
        sandbox="allow-scripts allow-popups allow-downloads allow-forms"
        className="min-h-0 flex-1 border-0 bg-white"
      />
    </div>
  );
}
