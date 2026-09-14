/**
 * The contract between the mount in `index.tsx` and the four app components.
 *
 * One bundle serves every app in this server, so each component agrees to the
 * same two props and to nothing else. Everything protocol-shaped (the
 * handshake, the tool result, host styling) is handled once at the mount, and a
 * component receives only the data it draws and the one call it can make.
 */
import type { ReactNode } from "react";

import type { CallToolResult } from "@modelcontextprotocol/client";

/**
 * A tool result's `structuredContent`, as it arrives off the wire.
 *
 * Untyped on purpose: this is JSON a host forwarded from the server, and no
 * amount of annotation here makes it so. Each component narrows it with its own
 * reader (see `readModel` in rebalance.tsx), which is also where a missing or
 * malformed field gets its default.
 */
export type AppData = Record<string, unknown>;

/**
 * Call a tool on this app's own server.
 *
 * The host proxies it, which is what SEP-1865 asks a host to do. This is how a
 * person's choice leaves the iframe: the server publishes a tool marked
 * `visibility: ["app"]`, invisible to the model, and the app calls it.
 */
export type CallTool = (
  name: string,
  args: Record<string, unknown>,
) => Promise<CallToolResult>;

/** What every app component in this bundle is handed. */
export interface AppProps {
  /**
   * The tool's own arguments, as they stand.
   *
   * Changes frame by frame while the model writes them, which is what lets an
   * app draw as they arrive instead of appearing finished. Empty until the
   * first notification.
   */
  args: AppData;
  /** The `structuredContent` of the tool result that opened this app. */
  data: AppData;
  /** The app's one way to send something back. */
  callTool: CallTool;
}

/** The shape `index.tsx` mounts. One per app, keyed by the mount's app name. */
export type AppComponent = (props: AppProps) => ReactNode;
