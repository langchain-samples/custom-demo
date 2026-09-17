/**
 * App documents, read once each and shared by every app on the page.
 *
 * The same tool called twice is the same document, and reading it again would
 * stall the second app behind an answer already in hand.
 */
import type { McpAppMetadata } from "./bindings";

export const MCP_APP_MIME_TYPE = "text/html;profile=mcp-app";

/** An app document, as `loadResource` returns it. */
export interface McpAppResource {
  uri: string;
  mimeType: string;
  html: string;
  /** The resource's `_meta`. Its `csp` becomes the view's policy. */
  meta?: { csp?: unknown; permissions?: unknown } | null;
}

const documents = new Map<string, Promise<McpAppResource>>();

/** Read a `ui://` document, or hand back the read already in flight. */
export function readDocument(
  uri: string,
  read: (app: McpAppMetadata) => Promise<McpAppResource>,
): Promise<McpAppResource> {
  const cached = documents.get(uri);
  if (cached) return cached;

  const pending = read({ resourceUri: uri, mimeType: MCP_APP_MIME_TYPE }).catch((err) => {
    // A failure must not be cached, or one flaky read breaks the app for the
    // rest of the session.
    documents.delete(uri);
    throw err;
  });
  documents.set(uri, pending);
  return pending;
}
