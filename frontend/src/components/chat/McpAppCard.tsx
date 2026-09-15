/**
 * An MCP App, rendered for a tool call that finished and ships its own UI.
 *
 * This is the ordinary MCP Apps flow, and the one every third-party server uses.
 * A tool carries `_meta.ui.resourceUri`; this page reads that `ui://` resource
 * with its own MCP client (see `lib/mcpClients.ts`, which reaches the server
 * through the deployment's byte proxy) and renders the HTML in a sandboxed
 * iframe, then hands it the tool's own result to draw. When a person does
 * something, the app calls a tool and the same client makes the call.
 *
 * Nothing pauses. The run has already moved on by the time this appears, which
 * is why the app can call tools freely and why there is no answer to give back.
 *
 * The iframe is sandboxed to `allow-scripts` ONLY. No `allow-same-origin`, so
 * the server's HTML has no route to this page's origin, cookies or storage. The
 * conversation with it is SEP-1865, in `lib/mcpAppHost.ts`.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { IconApps, IconX } from "@tabler/icons-react";
import type { McpServerConfig } from "@/lib/api";
import { callMcpToolForApp, readMcpApp, readMcpResource } from "@/lib/mcpClients";
import { createMcpAppHost, type McpToolResult } from "@/lib/mcpAppHost";

export interface McpAppCardProps {
  /** The tool whose app this is, namespaced as `{server}_{tool}`. */
  toolName: string;
  /**
   * What it was called with, as they stand.
   *
   * Streamed: this changes frame by frame while the model writes the arguments,
   * and each change reaches the app as `tool-input-partial`, which is what lets
   * a drawing app draw as it goes instead of appearing finished.
   */
  toolArguments: Record<string, unknown>;
  /** True while the arguments are still arriving. */
  streaming?: boolean;
  /** What it returned. Absent until the call finishes. */
  toolResult?: McpToolResult;
  /** MCP servers on the active assistant, needed to read the app and proxy calls. */
  servers: McpServerConfig[];
}

/**
 * The tool's own name, without the `{server}_` prefix.
 *
 * That prefix is ours, added by the ClientGroup so a remote `search` cannot
 * collide with our own. The person reading the card cares which tool ran, not
 * how we avoided a name collision.
 */
function toolLabel(toolName: string): string {
  return toolName.includes("_") ? toolName.split("_").slice(1).join("_") : toolName;
}

/**
 * Motion for the waiting state, borrowed from the artifact skeleton.
 *
 * Each bar is DRAWN left to right rather than appearing at full width, which
 * reads as something being produced instead of a box being filled. Reduced
 * motion keeps the bars and drops the movement.
 */
const SKELETON_KEYFRAMES = `
@keyframes mcp-app-bar-in {
  from { transform: scaleX(0.06); opacity: 0.45; }
  to { transform: scaleX(1); opacity: 1; }
}
@media (prefers-reduced-motion: reduce) {
  .mcp-app-bar { animation: none !important; }
}
`;

/**
 * What the pane shows between mounting and the first arguments arriving.
 *
 * The frame is mounted on the tool CALL so the app can draw as the arguments
 * stream, which means there is a window where the document is live and has
 * nothing to draw yet. Left alone that window is the app's own empty state, and
 * for a canvas app it is a black rectangle the height of the pane, which reads
 * as broken rather than as pending.
 *
 * Deliberately NOT a spinner. The bars stand where the app's own controls will
 * be, so the pane keeps its shape and the swap is a fill rather than a jump.
 */
function Waiting() {
  return (
    <div
      aria-hidden
      className="pointer-events-none absolute inset-0 flex flex-col gap-3 rounded-lg bg-panel-2 p-4"
    >
      <style>{SKELETON_KEYFRAMES}</style>
      {[
        ["45%", "0ms"],
        ["78%", "90ms"],
        ["62%", "180ms"],
        ["88%", "270ms"],
      ].map(([width, delay]) => (
        <div
          key={delay}
          className="mcp-app-bar h-2.5 origin-left rounded-full bg-border"
          style={{ width, animation: `mcp-app-bar-in 420ms ease-out ${delay} both` }}
        />
      ))}
    </div>
  );
}

/**
 * The app's own frame, once its HTML has been read.
 *
 * Fullscreen is a CSS change on a wrapper that is ALREADY in the tree, never a
 * move. Re-parenting the iframe (into a portal, say) reloads the document and
 * throws away whatever the person had done in it, which for a drawing app is
 * the whole of their work. So the element never moves and the tree shape never
 * changes: the exit bar is always rendered and merely `hidden` when inline.
 */
function AppFrame({
  toolName,
  toolArguments,
  toolResult,
  streaming,
  servers,
  html,
  inputSchema,
}: McpAppCardProps & { html: string; inputSchema?: Record<string, unknown> }) {
  const ref = useRef<HTMLIFrameElement | null>(null);
  const host = useRef<ReturnType<typeof createMcpAppHost> | null>(null);
  const [height, setHeight] = useState(320);
  const [mode, setMode] = useState("inline");
  // The app has something to draw once the model has written any arguments.
  // Sticky: a later frame that momentarily parses to `{}` must not put the
  // waiting state back over a drawing the person is already looking at.
  const [hasInput, setHasInput] = useState(false);

  useEffect(() => {
    const bridge = createMcpAppHost({
      toolName,
      toolInputSchema: inputSchema,
      // Clamped here rather than in the host: the ceiling is this card's
      // layout, and it is the same number the host advertises as maxHeight.
      onHeight: (h) => setHeight(Math.min(Math.max(h, 160), 640)),
      onToolCall: (name, args) => callMcpToolForApp(servers, toolName, name, args),
      onReadResource: (uri) => readMcpResource(servers, toolName, uri),
      // Excalidraw's Edit button asks for exactly this. Declining it, which is
      // all a host advertising inline-only can do, is why that button did
      // nothing.
      displayModes: ["inline", "fullscreen"],
      // `setMode` is stable, so granting a mode never rebuilds the host and so
      // never reloads the app.
      onDisplayMode: setMode,
    });
    host.current = bridge;
    // The SDK's transport owns the message listener now, so the only wiring
    // left is handing it the frame once. NOT on `onLoad`: the app opens its
    // handshake as soon as its inline script runs, which is before load fires,
    // and a transport attached late would miss `ui/initialize` entirely.
    const win = ref.current?.contentWindow;
    if (win) void bridge.connect(win).catch(() => {});
    return () => {
      bridge.teardown("The app was closed.");
      host.current = null;
    };
    // Deliberately NOT keyed on the arguments or the result. Those change on
    // every streamed frame, and rebuilding the host would tear down a handshake
    // the iframe never repeats: its document does not reload, so the app would
    // sit there talking to a host that had forgotten it.
  }, [toolName, servers, inputSchema]);

  // Feed the call in as it arrives.
  useEffect(() => {
    host.current?.setToolInput(toolArguments, !streaming);
    if (Object.keys(toolArguments).length > 0) setHasInput(true);
  }, [toolArguments, streaming]);

  useEffect(() => {
    if (toolResult) host.current?.setToolResult(toolResult);
  }, [toolResult]);

  /** Leave fullscreen, and tell the app so it can put its own chrome back. */
  const collapse = useCallback(() => {
    host.current?.setDisplayMode("inline");
    setMode("inline");
  }, []);

  useEffect(() => {
    if (mode !== "fullscreen") return;
    // Escape is what a person reaches for, and the app cannot hear the key once
    // focus has left its frame.
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") collapse();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mode, collapse]);

  const full = mode === "fullscreen";
  return (
    <div className={full ? "fixed inset-0 z-50 flex flex-col gap-2 bg-background p-3" : "contents"}>
      <div hidden={!full} className="flex items-center gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          {toolLabel(toolName)}
        </span>
        <span className="flex-1" />
        <button
          type="button"
          onClick={collapse}
          className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-[12px] hover:bg-panel"
        >
          <IconX size={13} /> Exit (Esc)
        </button>
      </div>
      <div
        // Always rendered, never conditional: a tree shape that changed would
        // re-parent the iframe, reloading the document and throwing away
        // whatever the person had done in it.
        className={`relative w-full${full ? " min-h-0 flex-1" : ""}`}
        style={full ? undefined : { height }}
      >
        <iframe
          ref={ref}
          title={`MCP app for ${toolName}`}
          srcDoc={html}
          // See the note above: allow-same-origin must never be added here.
          sandbox="allow-scripts"
          className="h-full w-full rounded-lg border border-border bg-background"
        />
        {!hasInput && <Waiting />}
      </div>
    </div>
  );
}

/**
 * The card, which is only anything at all when the tool actually ships a UI.
 *
 * Renders nothing while the lookup is in flight and nothing if there is no app,
 * rather than a placeholder: most tools have no UI, so a box saying so would
 * appear beside almost every call.
 */
export function McpAppCard(props: McpAppCardProps) {
  const { toolName, servers } = props;
  const [app, setApp] = useState<{ html: string; input_schema?: Record<string, unknown> } | null>(
    null,
  );

  useEffect(() => {
    let live = true;
    if (!toolName || !servers.length) return;
    void readMcpApp(servers, toolName).then((found) => {
      if (live) setApp(found ? { html: found.html, input_schema: found.inputSchema } : null);
    });
    return () => {
      live = false;
    };
  }, [toolName, servers]);

  if (!app) return null;

  return (
    // `fade-in` only, deliberately: `slide-in-from-bottom-1` animates a
    // transform, and a transformed ancestor becomes the containing block for
    // `position: fixed`, which would trap the fullscreen overlay inside this
    // card. Opacity creates no containing block.
    <div className="flex animate-in flex-col gap-2 rounded-xl border border-brand/40 bg-panel-2 p-3 duration-200 fade-in">
      <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-brand">
        <IconApps size={13} />
        {toolLabel(toolName)}
      </div>
      <AppFrame {...props} html={app.html} inputSchema={app.input_schema} />
    </div>
  );
}
