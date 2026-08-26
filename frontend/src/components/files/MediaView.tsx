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
    return (
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
