/**
 * A PDF or image from the sandbox, rendered rather than refused.
 *
 * The bytes arrive base64 in JSON (the VM has no static file server), and are turned into
 * a BLOB URL rather than fed to the element as a `data:` URI: Chrome blocks `data:`
 * documents in frames, so a data-URI PDF renders as a blank rectangle with nothing in the
 * console. The URL is revoked on unmount, or every file you click leaks its bytes for the
 * life of the tab.
 */
import { useEffect, useState } from "react";

/** base64 → Blob, chunked so a large file cannot blow the argument limit. */
function toBlob(b64: string, mime: string): Blob {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], { type: mime });
}

export function MediaView({ base64, mime, name }: { base64: string; mime: string; name: string }) {
  const [url, setUrl] = useState("");
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let objectUrl = "";
    try {
      objectUrl = URL.createObjectURL(toBlob(base64, mime));
      setUrl(objectUrl);
      setFailed(false);
    } catch {
      setFailed(true);
    }
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [base64, mime]);

  if (failed) {
    return <p className="m-0 text-sm text-destructive">This file could not be decoded.</p>;
  }
  if (!url) return null;

  if (mime === "application/pdf") {
    // A plain <iframe>: the browser's own PDF viewer already has paging, zoom and search,
    // and shipping a JS renderer to reproduce them would be a megabyte of dependency for
    // a preview pane.
    //
    // SECURITY. This is the one iframe in the app with NO `sandbox`, and it is deliberate.
    // Chrome routes a PDF to its viewer through a plugin/MimeHandler, and a sandboxed frame
    // cannot host one AT ALL: with any `sandbox` value the frame shows the browser's
    // "cannot be displayed" page instead of the document. Measured, not assumed - headless
    // Chrome screenshots of this exact blob-in-an-iframe with `sandbox` absent, `""`,
    // `allow-scripts`, `allow-scripts allow-popups allow-modals allow-downloads` and even
    // `allow-scripts allow-same-origin` render the PDF only in the first case, and a plain
    // same-origin `.pdf` URL behaves identically. There is no token that buys the isolation
    // back, so the choice is an unsandboxed viewer or no viewer.
    //
    // What keeps that acceptable is that the frame can never be an HTML document. `mime`
    // comes from the server's extension allowlist (`_MEDIA_MIME` in webapp.py: pdf, png,
    // jpeg, gif, webp and nothing else), this branch narrows it to `application/pdf`, and
    // the Blob is built with that same type - so the response the frame loads is always
    // served as a PDF and reaches PDFium, never the HTML parser. A hostile PDF's own
    // scripting runs inside the viewer, where it cannot touch this page, its DOM, or the
    // deployment token in `localStorage`. Contrast HtmlArtifact.tsx and
    // chat/McpElicitationCard.tsx, which DO render untrusted HTML and therefore must keep
    // `allow-scripts` without `allow-same-origin`.
    return (
      // oxlint-disable-next-line react/iframe-missing-sandbox -- see the SECURITY note above
      <iframe
        src={url}
        title={name}
        className="h-full min-h-[60vh] w-full rounded-md border border-border bg-background"
      />
    );
  }
  return (
    <img
      src={url}
      alt={name}
      className="max-w-full rounded-md border border-border bg-background"
    />
  );
}
