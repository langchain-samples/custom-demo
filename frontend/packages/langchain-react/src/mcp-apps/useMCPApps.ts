/**
 * Find the MCP Apps in a thread, without deciding where they go.
 *
 * Placement belongs to the application: an app is part of a conversation and
 * has to sit inside whatever markup that conversation is made of, next to the
 * turn that opened it. A component that rendered every app itself would be
 * making a layout decision it is in no position to make.
 *
 *     const mcpApps = useMCPApps(thread, { apps, loadResource });
 *
 *     {messages.map((message) => (
 *       <MyTurn key={message.id} message={message}>
 *         {mcpApps.forMessage(message.id).map((part) => (
 *           <MCPApp key={part.toolCallId} part={part} {...config} />
 *         ))}
 *       </MyTurn>
 *     ))}
 */
import { useEffect, useMemo, useRef } from "react";
import { mcpAppParts, type McpAppPart, type McpAppThread, type McpAppUri } from "./bindings";
import { readDocument, type McpAppResource } from "./documents";

export interface UseMCPAppsOptions {
  /**
   * Bindings by tool name, usually read once from the host's own route.
   *
   * Worth passing. With it an app is recognised the moment the model names
   * the tool, so its arguments can stream; without it the app waits for the
   * per-call stamp, which arrives once the message is already complete.
   */
  apps?: Record<string, McpAppUri>;
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
  /** The apps opened by one message, which is usually zero or one. */
  forMessage: (messageId: string) => McpAppPart[];
  /** Whether a tool result belongs to an app, and is therefore already drawn. */
  isAppResult: (toolCallId: string) => boolean;
}

/** The apps in a thread, grouped so they can be placed. */
export function useMCPApps(thread: McpAppThread, options: UseMCPAppsOptions = {}): MCPApps {
  const { apps, loadResource } = options;
  const read = useRef(loadResource);
  read.current = loadResource;

  useEffect(() => {
    if (!read.current) return;
    for (const uri of Object.values(apps ?? {})) {
      void readDocument(uri, read.current).catch(() => {});
    }
  }, [apps]);

  return useMemo(() => {
    const all = mcpAppParts(thread, apps);
    const byMessage = new Map<string, McpAppPart[]>();
    const callIds = new Set<string>();
    for (const part of all) {
      const group = byMessage.get(part.messageId);
      if (group) group.push(part);
      else byMessage.set(part.messageId, [part]);
      callIds.add(part.toolCallId);
    }

    return {
      all,
      forMessage: (messageId: string) => byMessage.get(messageId) ?? [],
      isAppResult: (toolCallId: string) => callIds.has(toolCallId),
    };
  }, [thread, apps]);
}
