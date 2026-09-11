/**
 * An MCP App, rendered for a tool call that finished and ships its own UI.
 *
 * This is the ordinary MCP Apps flow, and the one every third-party server uses.
 * A tool carries `_meta.ui.resourceUri`; the deployment reads that `ui://`
 * resource over MCP (a browser cannot speak it) and hands us the HTML; we render
 * it in a sandboxed iframe and hand it the tool's own result to draw. When a
 * person does something, the app calls a tool and we proxy it.
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
import {
  callMcpAppTool,
  fetchMcpApp,
  fetchMcpResource,
  type McpServerConfig,
} from "@/lib/api";
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

  useEffect(() => {
    const bridge = createMcpAppHost({
      toolName,
      toolInputSchema: inputSchema,
      // Clamped here rather than in the host: the ceiling is this card's
      // layout, and it is the same number the host advertises as maxHeight.
      onHeight: (h) => setHeight(Math.min(Math.max(h, 160), 640)),
      onToolCall: (name, args) => callMcpAppTool(servers, toolName, name, args),
      onReadResource: (uri) => fetchMcpResource(servers, toolName, uri),
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
      <iframe
        ref={ref}
        title={`MCP app for ${toolName}`}
        srcDoc={html}
        // See the note above: allow-same-origin must never be added here.
        sandbox="allow-scripts"
        className={`w-full rounded-lg border border-border bg-background${
          full ? " min-h-0 flex-1" : ""
        }`}
        style={full ? undefined : { height }}
      />
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
    void fetchMcpApp(servers, toolName).then((found) => {
      if (live) setApp(found ? { html: found.html, input_schema: found.input_schema } : null);
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
