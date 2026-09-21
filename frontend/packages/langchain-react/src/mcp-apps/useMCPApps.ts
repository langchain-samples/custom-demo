/**
 * Find the MCP Apps in a thread, without deciding where they go.
 *
 * Placement belongs to the application: an app is part of a conversation and
 * has to sit inside whatever markup that conversation is made of, next to the
 * turn that opened it. A component that rendered every app itself would be
 * making a layout decision it is in no position to make.
 *
 * An app is one of a message's tool calls, drawn differently, so it is placed
 * in the loop a host already runs over those calls:
 *
 *     const mcpApps = useMCPApps(thread, { apps, loadResource });
 *
 *     {message.tool_calls.map((call) => {
 *       const app = mcpApps.forCall(call.id);
 *       return app
 *         ? <MCPApp key={call.id} part={app} {...config} />
 *         : <MyToolChip key={call.id} call={call} />;
 *     })}
 */
import { useEffect, useMemo, useRef } from "react";
import { mcpAppParts, type McpAppPart, type McpAppThread, type McpAppUri } from "./bindings";
import { readDocument, type McpAppResource } from "./documents";

export interface UseMCPAppsOptions {
  /**
   * Which tool names ship a UI, and the `ui://` document each opens.
   *
   * Required, because it is the only thing that says a call is an app. A host
   * reads it once from its own route, the same route that reads a document
   * and proxies a view's tool call, and passes it here. Answering once is
   * enough: `resourceUri` is declared on the tool and never varies per call.
   */
  apps: Record<string, McpAppUri>;
  /**
   * Passing this reads the app documents up front.
   *
   * The window in which arguments can stream is the gap between the model
   * naming a tool and the view being ready to hear, and reading the document
   * is the largest thing in it.
   */
  loadResource?: (uri: McpAppUri) => Promise<McpAppResource>;
}

export interface MCPApps {
  /** Every app in the thread, in the order their calls were made. */
  all: McpAppPart[];
  /**
   * The app a tool call opens, or undefined for an ordinary tool.
   *
   * The granularity a conversation renders at: a host already loops over a
   * message's `tool_calls` to draw them, and an app is one of those calls
   * drawn differently, in its place among the others.
   */
  forCall: (toolCallId: string) => McpAppPart | undefined;
  /** Whether a tool result belongs to an app, and is therefore already drawn. */
  isAppResult: (toolCallId: string) => boolean;
}

/** The apps in a thread, grouped so they can be placed. */
export function useMCPApps(thread: McpAppThread, options: UseMCPAppsOptions): MCPApps {
  const { apps, loadResource } = options;
  const read = useRef(loadResource);
  read.current = loadResource;

  useEffect(() => {
    if (!read.current) return;
    for (const uri of Object.values(apps)) {
      void readDocument(uri, read.current).catch(() => {});
    }
  }, [apps]);

  return useMemo(() => {
    const all = mcpAppParts(thread, apps);
    const byCall = new Map<string, McpAppPart>();
    for (const part of all) byCall.set(part.toolCallId, part);

    return {
      all,
      forCall: (toolCallId: string) => byCall.get(toolCallId),
      isAppResult: (toolCallId: string) => byCall.has(toolCallId),
    };
  }, [thread, apps]);
}
