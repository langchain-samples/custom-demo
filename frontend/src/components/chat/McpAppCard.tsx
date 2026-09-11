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
import { useEffect, useRef, useState } from "react";
import { IconApps } from "@tabler/icons-react";
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
  /** What it was called with, replayed to the app. */
  toolArguments: Record<string, unknown>;
  /** What it returned, which is the data the app draws. */
  toolResult: McpToolResult;
  /** MCP servers on the active assistant, needed to read the app and proxy calls. */
  servers: McpServerConfig[];
}

/** The app's own frame, once its HTML has been read. */
function AppFrame({
  toolName,
  toolArguments,
  toolResult,
  servers,
  html,
  inputSchema,
}: McpAppCardProps & { html: string; inputSchema?: Record<string, unknown> }) {
  const ref = useRef<HTMLIFrameElement | null>(null);
  const [height, setHeight] = useState(320);

  useEffect(() => {
    const host = createMcpAppHost({
      toolName,
      toolArguments,
      toolResult,
      toolInputSchema: inputSchema,
      // Clamped here rather than in the host: the ceiling is this card's layout,
      // and it is the same number the host advertises as maxHeight.
      onHeight: (h) => setHeight(Math.min(Math.max(h, 160), 640)),
      onToolCall: (name, args) => callMcpAppTool(servers, toolName, name, args),
      onReadResource: (uri) => fetchMcpResource(servers, toolName, uri),
    });
    const view = () => ref.current?.contentWindow ?? null;
    const onMessage = (event: MessageEvent) => host.handleMessage(event, view());
    window.addEventListener("message", onMessage);
    return () => {
      window.removeEventListener("message", onMessage);
      host.teardown(view(), "The app was closed.");
    };
  }, [toolName, toolArguments, toolResult, servers, inputSchema]);

  return (
    <iframe
      ref={ref}
      title={`MCP app for ${toolName}`}
      srcDoc={html}
      // See the note above: allow-same-origin must never be added here.
      sandbox="allow-scripts"
      className="w-full rounded-lg border border-border bg-background"
      style={{ height }}
    />
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

  const label = toolName.includes("_") ? toolName.split("_").slice(1).join("_") : toolName;
  return (
    <div className="flex animate-in flex-col gap-2 rounded-xl border border-brand/40 bg-panel-2 p-3 duration-200 fade-in slide-in-from-bottom-1">
      <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-brand">
        <IconApps size={13} />
        {label}
      </div>
      <AppFrame {...props} html={app.html} inputSchema={app.input_schema} />
    </div>
  );
}
