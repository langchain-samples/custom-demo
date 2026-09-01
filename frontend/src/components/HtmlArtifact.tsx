/**
 * One HTML artifact, rendered live in a sandboxed iframe.
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
 * The `srcDoc` swap is debounced. `write_file`'s content argument streams a token at a
 * time, and reassigning `srcDoc` reloads the frame, so a swap per token would restart
 * the document (and re-run its scripts) tens of times a second.
 */
import { useEffect, useRef, useState } from "react";
import { IconDownload } from "@tabler/icons-react";
import { Button } from "@/components/ui/button";
import { artifactName, safeHtmlPrefix } from "@/lib/artifacts";

/** Frames per second cap on reloading the iframe while content streams. */
const RENDER_DEBOUNCE_MS = 120;

export interface HtmlArtifactProps {
  /** Absolute path on the agent's filesystem; also the tab key. */
  path: string;
  /** Content so far. Partial while `streaming`, canonical once the write completes. */
  content: string;
  /** True while write_file's args are still arriving. */
  streaming?: boolean;
}

/** Live preview of one HTML artifact, with a download button. */
export function HtmlArtifact({ path, content, streaming }: HtmlArtifactProps) {
  const name = artifactName(path);
  // What the iframe is actually showing. Lags `content` by up to the debounce.
  const [rendered, setRendered] = useState(() => safeHtmlPrefix(content));
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

  const download = () => {
    const blob = new Blob([content], { type: "text/html;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 px-4 py-2">
        <span className="truncate font-mono text-xs text-muted-foreground">{path}</span>
        {streaming && (
          <span className="flex-shrink-0 rounded bg-panel-2 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
            Writing
          </span>
        )}
        <Button
          variant="ghost"
          size="sm"
          onClick={download}
          disabled={!content}
          className="ml-auto flex-shrink-0"
          title={`Download ${name}`}
        >
          <IconDownload className="size-4" />
          Download
        </Button>
      </div>
      <iframe
        // Remounting per path (rather than reusing one frame) keeps two artifacts from
        // inheriting each other's scroll position and script state.
        key={path}
        title={name}
        srcDoc={rendered}
        // See the SECURITY note above: allow-same-origin must never be added here.
        sandbox="allow-scripts allow-popups allow-downloads allow-forms"
        className="min-h-0 flex-1 border-0 bg-white"
      />
    </div>
  );
}
