/**
 * React pieces for LangGraph hosts.
 *
 * Two ways in. `useMCPApps` finds the apps in a thread and `MCPApp` renders
 * one, so a conversation can place them among its own components. For a host
 * that does not care where they land, `experimental_MCPAppRenderer` does both
 * at once.
 *
 * `experimental_` because SEP-1865 is young and this surface will move with it.
 */
export {
  MCPAppRenderer as experimental_MCPAppRenderer,
  MCPApp as experimental_MCPApp,
  type MCPAppRendererProps,
  type MCPAppProps,
  type McpAppConfig,
  type McpAppHandlers,
} from "./mcp-apps/MCPAppRenderer";
export { useMCPApps as experimental_useMCPApps, type MCPApps, type UseMCPAppsOptions } from "./mcp-apps/useMCPApps";
export type { McpAppPart, McpAppUri } from "./mcp-apps/bindings";
export type { McpAppResource } from "./mcp-apps/documents";
