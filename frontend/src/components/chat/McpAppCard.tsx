/**
 * An MCP App, rendered for a tool call that ships its own UI.
 *
 * This is the ordinary MCP Apps flow, and the one every third-party server
 * uses. A tool carries `_meta.ui.resourceUri`; this page reads that `ui://`
 * resource with its own MCP client (see `lib/mcpClients.ts`, which reaches the
 * server through the deployment's byte proxy) and `MCPApp` renders it, hands
 * it the tool's own result, and carries what it sends back.
 *
 * The SEP-1865 half lives in `@langchain/react` rather than here: the
 * handshake, the lifecycle ordering and the sandboxing are the same wherever
 * an app is rendered, and this file is only the card around one.
 *
 * Nothing pauses. The run has already moved on by the time this appears, which
 * is why the app can call tools freely and why there is no answer to give back.
 *
 * ONE DEVIATION, DELIBERATE. SEP-1865 says a web host MUST wrap the view in a
 * different-origin sandbox proxy, so a view can hold `allow-same-origin`
 * without holding the host's origin. We serve the SPA from one origin and have
 * nowhere to put a second, so `direct` renders the view with `allow-scripts`
 * and never `allow-same-origin`. Stricter than the proxy it replaces: the view
 * gets an opaque origin and therefore no network at all. An app that needs
 * same-origin will not run here.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { IconApps, IconX } from "@tabler/icons-react";
import { experimental_MCPApp as MCPApp } from "@langchain/react";
import type { McpAppPart, McpAppResource } from "@langchain/react";
import type { McpServerConfig } from "@/lib/api";
import { callMcpToolForApp, readMcpApp, readMcpResource } from "@/lib/mcpClients";
import { skeletonReveal } from "@/lib/artifacts";
import { themeVariables } from "@/lib/mcpAppTheme";

/** A tool result as the host hands it to a view. */
export interface McpToolResult {
  structuredContent?: unknown;
  content?: unknown[];
}

export interface McpAppCardProps {
  /** The tool whose app this is, namespaced as `{server}_{tool}`. */
  toolName: string;
  /** The tool call this app belongs to, which is its identity to the bridge. */
  toolCallId: string;
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
  toolCallId,
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
    if (hasInput) return;
    const startedAt = Date.now();
    const tick = setInterval(() => setWaitedMs(Date.now() - startedAt), 120);
    return () => clearInterval(tick);
  }, [hasInput]);

  useEffect(() => {
    if (Object.keys(toolArguments).length > 0) setHasInput(true);
  }, [toolArguments]);

  /**
   * The tool call, in the shape the renderer reads.
   *
   * Built here rather than discovered from a thread because this SPA keeps
   * its own conversation model: the renderer only ever needs the call, the
   * binding and where the arguments have got to.
   */
  const part: McpAppPart = useMemo(
    () => ({
      toolCallId,
      toolName,
      messageId: toolCallId,
      uri: resource.uri,
      input: toolArguments,
      output: toolResult as McpAppPart["output"],
      streaming: Boolean(streaming),
    }),
    [toolCallId, toolName, resource.uri, toolArguments, toolResult, streaming],
  );

  // Already read, by the lookup that found the binding. Resolving from hand
  // keeps the renderer from asking the server for a document we are holding.
  const loadResource = useCallback(async () => resource, [resource]);

  const handlers = useMemo(
    () => ({
      callTool: ({ name, arguments: args }: { name: string; arguments: Record<string, unknown> }) =>
        callMcpToolForApp(servers, toolName, name, args),
      readResource: ({ uri }: { uri: string }) => readMcpResource(servers, toolName, uri),
      // Clamped here rather than in the renderer: the ceiling is this card's
      // layout, and it is the same number the host advertises as maxHeight.
      onResize: ({ height: h }: { height: number }) => setHeight(Math.min(Math.max(h, 160), 640)),
      onDisplayMode: setMode,
    }),
    [servers, toolName],
  );

  const hostContext = useMemo(
    () => ({
      theme: document.documentElement.classList.contains("dark") ? "dark" : "light",
      styles: { variables: themeVariables() },
      displayMode: mode,
      // Excalidraw's Edit button asks for exactly this. Declining it, which is
      // all a host advertising inline-only can do, is why that button did
      // nothing.
      availableDisplayModes: ["inline", "fullscreen"],
      containerDimensions: { maxHeight: 640 },
      locale: navigator.language,
      timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    }),
    [mode],
  );

  /** Leave fullscreen. The renderer tells the app, which restores its chrome. */
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
          app={part}
          // One origin here, so no proxy. See the note at the top of the file.
          sandbox={{
            direct: true,
            className: "h-full w-full rounded-lg border border-border bg-background",
            style: {},
          }}
          loadResource={loadResource}
          handlers={handlers}
          hostInfo={{ name: "custom-demos-spa", version: "1.0.0" }}
          hostContext={hostContext}
          toolInputSchema={inputSchema}
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
