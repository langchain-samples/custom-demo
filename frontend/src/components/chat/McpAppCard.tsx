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
 * the server's HTML has no route to this page's origin, cookies or storage.
 *
 * The SEP-1865 conversation itself is `experimental_MCPApp` from the SDK: the
 * handshake, the lifecycle, the input ordering and the frame are the same in
 * every host, and this file is the card around one. What stays here is what is
 * ours: which resource to read, who may call what, the waiting state, and the
 * fullscreen chrome.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { IconApps, IconX } from "@tabler/icons-react";
import { experimental_MCPApp as MCPApp } from "@langchain/langgraph-sdk/react";
import type { McpAppCall, McpAppResource } from "@langchain/langgraph-sdk/react";
import type { McpServerConfig } from "@/lib/api";
import { callMcpToolForApp, readMcpApp, readMcpResource } from "@/lib/mcpClients";
import { skeletonReveal } from "@/lib/artifacts";

/** A tool result as the host hands it to a view. */
export interface McpToolResult {
  structuredContent?: unknown;
  content?: unknown[];
}

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
 * Motion for the waiting state, shared with the artifact skeleton.
 *
 * Each bar is DRAWN left to right rather than appearing at full width, which
 * reads as something being produced instead of a box being filled. Reduced
 * motion keeps the bars and drops the movement.
 */
const SKELETON_KEYFRAMES = `
@keyframes mcp-app-row-in {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: none; }
}
@keyframes mcp-app-bar-in {
  from { transform: scaleX(0.06); opacity: 0.45; }
  to { transform: scaleX(1); opacity: 1; }
}
@media (prefers-reduced-motion: reduce) {
  .mcp-app-row, .mcp-app-bar { animation: none !important; }
}
`;

/** Bar widths per row, so the pane fills unevenly the way a UI does. */
const SKELETON_ROWS = [
  ["46%"],
  ["88%", "64%"],
  ["72%"],
  ["94%", "52%"],
  ["60%"],
  ["82%", "70%"],
];

/**
 * What the pane shows between mounting and the first arguments arriving.
 *
 * The frame is mounted on the tool CALL so the app can draw as the arguments
 * stream, which means there is a window where the document is live and has
 * nothing to draw yet. Left alone that window is the app's own empty state, and
 * for a canvas app it is a black rectangle the height of the pane, which reads
 * as broken rather than as pending.
 *
 * It GROWS on a clock, like the artifact skeleton and for the same reason: a
 * fixed set of bars that draws once and then sits there says the pane is
 * finished and empty. Rows arriving say something is still coming.
 *
 * Deliberately not a spinner. The bars stand where the app's own controls will
 * be, so the pane keeps its shape and the swap is a fill rather than a jump.
 */
function Waiting({ reveal }: { reveal: number }) {
  // At least one row from the start, so the pane is never blank.
  const shown = Math.max(1, Math.round(reveal * SKELETON_ROWS.length));
  return (
    <div
      aria-hidden
      className="pointer-events-none absolute inset-0 flex flex-col gap-5 overflow-hidden rounded-lg bg-panel-2 p-5"
    >
      <style>{SKELETON_KEYFRAMES}</style>
      {SKELETON_ROWS.slice(0, shown).map((widths, row) => (
        <div
          key={row}
          // Only the newest row pulses. Pulsing all of them makes the whole
          // pane throb and hides the fact that rows are arriving at all.
          className={`mcp-app-row flex flex-col gap-2${row === shown - 1 ? " animate-pulse" : ""}`}
          style={{ animation: "mcp-app-row-in 420ms cubic-bezier(0.22, 1, 0.36, 1) both" }}
        >
          {widths.map((width, bar) => (
            <div
              key={width}
              className="mcp-app-bar h-2.5 origin-left rounded-full bg-border"
              style={{ width, animation: `mcp-app-bar-in 420ms ease-out ${bar * 90}ms both` }}
            />
          ))}
        </div>
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
  resource,
  inputSchema,
}: McpAppCardProps & { resource: McpAppResource; inputSchema?: Record<string, unknown> }) {
  const [height, setHeight] = useState(320);
  const [mode, setMode] = useState("inline");
  // The app has something to draw once the model has written any arguments.
  // Sticky: a later frame that momentarily parses to `{}` must not put the
  // waiting state back over a drawing the person is already looking at.
  const [hasInput, setHasInput] = useState(false);
  // Drives the skeleton's growth. Only ticks while there is nothing to draw,
  // so a rendered app is not re-rendering on a timer.
  const [waitedMs, setWaitedMs] = useState(0);

  useEffect(() => {
    if (Object.keys(toolArguments).length > 0) setHasInput(true);
  }, [toolArguments]);

  useEffect(() => {
    if (hasInput) return;
    const startedAt = Date.now();
    const tick = setInterval(() => setWaitedMs(Date.now() - startedAt), 120);
    return () => clearInterval(tick);
  }, [hasInput]);

  /** Leave fullscreen. `hostContext` tells the app, so it can restore its chrome. */
  const collapse = useCallback(() => setMode("inline"), []);

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

  /** The call, in the shape the renderer takes. */
  const call: McpAppCall = useMemo(
    () => ({
      toolName,
      resource,
      input: toolArguments,
      output: toolResult
        ? { content: toolResult.content ?? [], structuredContent: toolResult.structuredContent }
        : undefined,
      streaming: Boolean(streaming),
    }),
    [toolName, resource, toolArguments, toolResult, streaming],
  );

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
        <MCPApp
          app={call}
          // `direct`, not a proxy: we serve the SPA from one origin and have
          // nowhere to put a second. Stricter than the proxy it replaces, since
          // the view gets an opaque origin and therefore no network at all.
          sandbox={{
            direct: true,
            className: "h-full w-full rounded-lg border border-border bg-background",
          }}
          toolInputSchema={inputSchema}
          hostInfo={{ name: "custom-demo", version: "0.1.0" }}
          // Excalidraw's Edit button asks for fullscreen. Declining it, which is
          // all a host advertising inline-only can do, is why that button did
          // nothing.
          hostContext={{ displayMode: mode, availableDisplayModes: ["inline", "fullscreen"] }}
          // Never wired straight to the MCP client: `callMcpToolForApp` is
          // where a tool the server did not open to apps is refused, and a view
          // is server-authored HTML.
          callTool={({ name, arguments: args, app }) =>
            callMcpToolForApp(servers, app.toolName, name, args)
          }
          readResource={({ uri, app }) => readMcpResource(servers, app.toolName, uri)}
          // Clamped here rather than in the renderer: the ceiling is this
          // card's layout.
          onResize={({ height: h }) => setHeight(Math.min(Math.max(h, 160), 640))}
          onDisplayMode={setMode}
        />
        {!hasInput && <Waiting reveal={skeletonReveal(waitedMs)} />}
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
  const [app, setApp] = useState<{
    resource: McpAppResource;
    inputSchema?: Record<string, unknown>;
  } | null>(null);

  useEffect(() => {
    let live = true;
    if (!toolName || !servers.length) return;
    void readMcpApp(servers, toolName).then((found) => {
      if (!live) return;
      setApp(
        found
          ? {
              resource: {
                uri: found.resourceUri,
                mimeType: "text/html;profile=mcp-app",
                html: found.html,
              },
              inputSchema: found.inputSchema,
            }
          : null,
      );
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
      <AppFrame {...props} resource={app.resource} inputSchema={app.inputSchema} />
    </div>
  );
}
