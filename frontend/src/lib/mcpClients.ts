/**
 * A real MCP client, in the browser, over the deployment's proxy.
 *
 * The SPA is half of one Host and this is the half that speaks MCP. Everything
 * it needs, which tools exist, which ship a UI, what a `ui://` resolves to and
 * a view's own `tools/call`, is answered by `tools/list`, `resources/read` and
 * `tools/call` against these clients. The deployment no longer answers any of
 * those questions, and no longer knows they were asked.
 *
 * WHY A PROXY AND NOT THE SERVER DIRECTLY. An MCP server is a third-party
 * origin that need not send CORS headers, and the bearer token would have to
 * be in page JavaScript. `POST /mcp/proxy/{server_id}` forwards bytes to a
 * server the assistant is already configured for and attaches the credential
 * there. Claude reaches the same shape at
 * `/v1/toolbox/shttp/mcp/<connection-uuid>`; ChatGPT wraps the same calls in
 * bespoke REST instead. Both proxy; neither calls an MCP server from the page.
 *
 * WHY A CLIENT PER SERVER, HELD OPEN. Streamable HTTP is a session: the
 * `initialize` handshake is paid once and `mcp-session-id` carries it. A
 * client per call would re-handshake on every message and lose any
 * server-side session state a view depends on.
 */
import { Client, StreamableHTTPClientTransport } from "@modelcontextprotocol/client";
import type { Tool } from "@modelcontextprotocol/client";
import { apiHeaders, getApiBase, getAssistantId } from "./config";
import { mcpServerId, type McpProbeResult, type McpServerConfig } from "./api";

/** One connected server's client, keyed so a config change opens a new one. */
const CLIENTS = new Map<string, Promise<Client>>();

/** Identity + assistant + URL: any change has to mean a different connection. */
function clientKey(server: McpServerConfig): string {
  return `${getAssistantId()}\n${mcpServerId(server)}\n${server.url}`;
}

/**
 * The proxy endpoint for one server.
 *
 * The server is named by ID, never by URL. The deployment resolves it against
 * the assistant's own configuration, so this cannot be pointed anywhere the
 * assistant is not already connected, and the bearer token is attached there
 * rather than existing here.
 */
function proxyUrl(server: McpServerConfig): URL {
  const url = new URL(`${getApiBase()}/mcp/proxy/${encodeURIComponent(mcpServerId(server))}`);
  url.searchParams.set("assistant", getAssistantId());
  return url;
}

/** Connect to one server, or reuse the connection we already have. */
export function mcpClient(server: McpServerConfig): Promise<Client> {
  const key = clientKey(server);
  const open = CLIENTS.get(key);
  if (open) return open;

  const connecting = (async () => {
    const client = new Client({ name: "custom-demos-spa", version: "1.0.0" });
    await client.connect(
      new StreamableHTTPClientTransport(proxyUrl(server), {
        // Authenticates us to the DEPLOYMENT, not to the MCP server. The proxy
        // strips it and attaches the server's own credential instead.
        requestInit: { headers: apiHeaders() },
      }),
    );
    return client;
  })().catch((err) => {
    // A failed connection must not be remembered, or one blip disconnects a
    // server for the life of the page.
    CLIENTS.delete(key);
    throw err;
  });

  CLIENTS.set(key, connecting);
  return connecting;
}

/** Drop every connection. For a settings change, and for tests. */
export function resetMcpClients(): void {
  for (const pending of CLIENTS.values()) {
    void pending.then((c) => c.close()).catch(() => {});
  }

  CLIENTS.clear();
}

/** A tool as the SPA sees it: namespaced, and knowing which server it is on. */
export interface McpToolEntry {
  /** `{server}_{tool}`, matching what the agent's tool calls are named. */
  name: string;
  /** The tool's own name, which is what the server answers to. */
  upstreamName: string;
  server: McpServerConfig;
  tool: Tool;
}

/** The `ui` half of a tool's `_meta`, in either spelling the spec allows. */
function uiMeta(tool: Tool): { resourceUri?: string; visibility?: string[] } {
  const meta = (tool._meta ?? {}) as Record<string, unknown>;
  const ui = (meta.ui ?? {}) as { resourceUri?: string; visibility?: string[] };
  // The flat key is the extension's earlier spelling, deprecated but kept
  // until GA, and Excalidraw still sends both.
  const flat = meta["ui/resourceUri"];
  return {
    resourceUri: ui.resourceUri ?? (typeof flat === "string" ? flat : undefined),
    visibility: ui.visibility,
  };
}

/** The `ui://` document a tool renders, or undefined for an ordinary tool. */
export function toolAppUri(tool: Tool): string | undefined {
  const uri = uiMeta(tool).resourceUri;
  return uri?.startsWith("ui://") ? uri : undefined;
}

/**
 * Whether a view may call this tool (`visibility` includes `"app"`).
 *
 * SEP-1865 makes refusing a tool/call from an app a host MUST, and the host is
 * this page. That is real enforcement rather than a request, because the view
 * has an opaque origin and no network of its own: `postMessage` to us is its
 * only way out, so it cannot reach the proxy behind our back. Give the view
 * network access, by adding `allow-same-origin` or a same-origin sandbox
 * proxy, and this check stops being a boundary and becomes a suggestion.
 */
export function appCallable(tool: Tool): boolean {
  const visibility = uiMeta(tool).visibility;
  return !Array.isArray(visibility) || visibility.includes("app");
}

/** Whether the agent is allowed to see it, mirroring `model_visible`. */
export function modelVisible(tool: Tool): boolean {
  const visibility = uiMeta(tool).visibility;
  return !Array.isArray(visibility) || visibility.includes("model");
}

/**
 * Every tool on every connected server, namespaced as the agent names them.
 *
 * `{server}_{tool}` has to match what `ClientGroup` does on the deployment, or
 * a streamed tool call matches nothing: `mcpServerId` mirrors the backend's
 * `slugify` and a contract test pins the two together.
 *
 * Per server, so one dead connection costs only its own tools. A rejection
 * here is a server that would not connect, which the caller reports.
 */
export async function listMcpTools(
  servers: McpServerConfig[],
): Promise<{ entries: McpToolEntry[]; errors: Record<string, string> }> {
  const errors: Record<string, string> = {};
  const found = await Promise.all(
    servers.map(async (server) => {
      const id = mcpServerId(server);
      try {
        const client = await mcpClient(server);
        const { tools } = await client.listTools();
        return tools.map((tool) => ({
          name: `${id}_${tool.name}`,
          upstreamName: tool.name,
          server,
          tool,
        }));
      } catch (err) {
        errors[id] = err instanceof Error ? err.message : String(err);
        return [];
      }
    }),
  );

  return { entries: found.flat(), errors };
}

/* --------------------------- What the SPA asks --------------------------- */

/** Matches the deployment's tool-list TTL, so the two expire together. */
const TOOLS_TTL_MS = 120_000;
const TOOLS = new Map<string, { at: number; listed: ReturnType<typeof listMcpTools> }>();

/** The server set as a cache key, so editing a connection re-lists. */
function serverSetKey(servers: McpServerConfig[]): string {
  return servers.map((s) => `${mcpServerId(s)} ${s.url}`).join("|");
}

/**
 * Every tool, cached for the same window the deployment uses.
 *
 * `listTools` is a round trip per server and the answer feeds the mount
 * decision on every streamed tool call, so it cannot be asked per call.
 */
export function mcpTools(servers: McpServerConfig[]): ReturnType<typeof listMcpTools> {
  if (!servers.length) return Promise.resolve({ entries: [], errors: {} });
  const key = serverSetKey(servers);
  const hit = TOOLS.get(key);
  if (hit && Date.now() - hit.at < TOOLS_TTL_MS) return hit.listed;

  const listed = listMcpTools(servers);
  TOOLS.set(key, { at: Date.now(), listed });
  return listed;
}

/** Forget the cached tool lists, and any connection behind them. */
export function resetMcpTools(): void {
  TOOLS.clear();
  DOCS.clear();
  resetMcpClients();
}

/** What a tool's MCP App binding looks like to the host's browser half. */
export interface McpAppBinding {
  resourceUri: string;
  inputSchema?: Record<string, unknown>;
  appOnly?: boolean;
}

/**
 * Which tools ship a UI, by namespaced name.
 *
 * What the chat needs, and the reason a tool-name prefix was never enough: a
 * prefix says a tool came from an MCP server, not that it has a UI.
 */
export async function mcpAppBindings(
  servers: McpServerConfig[],
): Promise<Record<string, McpAppBinding>> {
  const { entries } = await mcpTools(servers);
  const apps: Record<string, McpAppBinding> = {};
  for (const entry of entries) {
    const resourceUri = toolAppUri(entry.tool);
    if (!resourceUri) continue;
    apps[entry.name] = {
      resourceUri,
      // `Tool` requires `inputSchema` and the app SDK validates the initialize
      // result, so a host that cannot fill `hostContext.toolInfo.tool` is
      // refused outright by every app built on it.
      inputSchema: (entry.tool.inputSchema as Record<string, unknown>) ?? { type: "object" },
      appOnly: !modelVisible(entry.tool),
    };
  }

  return apps;
}

/** One entry by its namespaced name, or undefined. */
async function entryFor(
  servers: McpServerConfig[],
  toolName: string,
): Promise<McpToolEntry | undefined> {
  const { entries } = await mcpTools(servers);
  return entries.find((e) => e.name === toolName);
}

/** One block of a resource, as a view receives it. */
export interface McpResourceContent {
  uri: string;
  mimeType?: string | null;
  text?: string;
  blob?: string;
}

/** A resource read on behalf of a view, on the server its tool came from. */
export async function readMcpResource(
  servers: McpServerConfig[],
  toolName: string,
  uri: string,
): Promise<McpResourceContent[]> {
  const entry = await entryFor(servers, toolName);
  if (!entry) throw new Error(`${toolName} is not a tool on any connected server`);
  const client = await mcpClient(entry.server);
  const { contents } = await client.readResource({ uri });
  return contents as McpResourceContent[];
}

/** App documents, keyed by the `ui://` URI and the server it came from. */
const DOCS = new Map<string, { at: number; html: Promise<string | null> }>();

/**
 * The HTML an MCP App renders, or null when the tool ships none.
 *
 * Keyed on the resource URI, which is OpenAI's own advice for their apps
 * ("treat the resource URI as a cache key. When you make a breaking change to
 * the HTML, publish a new URI"), plus the server, since two servers can
 * publish the same `ui://` path. Excalidraw's document is 432KB and ours is
 * 618KB, so a card that re-read it on every mount would be expensive.
 */
export async function readMcpApp(
  servers: McpServerConfig[],
  toolName: string,
): Promise<{ resourceUri: string; html: string; inputSchema?: Record<string, unknown> } | null> {
  const binding = (await mcpAppBindings(servers))[toolName];
  if (!binding) return null;

  const entry = await entryFor(servers, toolName);
  const key = `${binding.resourceUri}|${entry?.server.url ?? ""}`;
  const hit = DOCS.get(key);
  let pending = hit && Date.now() - hit.at < TOOLS_TTL_MS ? hit.html : undefined;
  if (!pending) {
    pending = readMcpResource(servers, toolName, binding.resourceUri)
      .then((contents) => contents.find((c) => c.text?.trim())?.text ?? null)
      .catch(() => {
        // Never remembered: the next render should retry rather than inherit
        // a network blip for the whole window.
        DOCS.delete(key);
        return null;
      });
    DOCS.set(key, { at: Date.now(), html: pending });
  }

  const html = await pending;
  if (!html) return null;
  return { resourceUri: binding.resourceUri, html, inputSchema: binding.inputSchema };
}

/**
 * Which tool a view's `tools/call` is allowed to reach, or why it is not.
 *
 * The two rules SEP-1865 makes a host enforce, kept pure and separate from the
 * call itself so they are testable without a connection. Both are refusals
 * rather than filters, and both reach the view as errors: a dropped request is
 * a button that hangs.
 */
export function resolveAppCall(
  entries: McpToolEntry[],
  appTool: string,
  target: string,
): McpToolEntry {
  const opener = entries.find((e) => e.name === appTool);
  if (!opener) throw new Error(`${appTool} is not a tool on any connected server`);

  // The app's own namespace first: a view calls tools by their unprefixed
  // names, because that is what its server published.
  const wanted =
    entries.find((e) => e.server === opener.server && e.upstreamName === target) ??
    entries.find((e) => e.name === target);
  if (!wanted) throw new Error(`${target} is not a tool on ${mcpServerId(opener.server)}`);

  // Cross-server calls from an app are always blocked.
  if (wanted.server !== opener.server) {
    throw new Error(`${appTool} may not call ${target}: they are on different servers`);
  }

  if (!appCallable(wanted.tool)) {
    throw new Error(`${target} is not open to apps: its visibility does not include 'app'`);
  }

  return wanted;
}

/** Make the call a view asked for, once `resolveAppCall` has allowed it. */
export async function callMcpToolForApp(
  servers: McpServerConfig[],
  appTool: string,
  target: string,
  args: Record<string, unknown>,
): Promise<{ structuredContent?: unknown; content?: unknown[] }> {
  const { entries } = await mcpTools(servers);
  const wanted = resolveAppCall(entries, appTool, target);
  const client = await mcpClient(wanted.server);
  const result = await client.callTool({ name: wanted.upstreamName, arguments: args });
  return {
    structuredContent: result.structuredContent,
    content: (result.content ?? []) as unknown[],
  };
}

/**
 * Connect to one server and report what it offers.
 *
 * Goes through this page's own MCP client, so what it reports is the
 * connection the chat will actually use rather than a second opinion from the
 * deployment. The tool list, the `app` badge and the failure reason all come
 * from one `initialize` + `tools/list`.
 *
 * SAVE FIRST. The proxy resolves a server by ID against the assistant's stored
 * configuration, which is what stops it being a general-purpose fetcher, and a
 * server still being typed is not there yet. Testing an unsaved draft would
 * need the deployment to accept a URL from the browser, which is the hole that
 * design closes, so the caller saves and then tests.
 */
export async function describeMcpServer(
  server: McpServerConfig,
  { reconnect = false }: { reconnect?: boolean } = {},
): Promise<McpProbeResult> {
  const id = mcpServerId(server);
  const base = { id, label: server.label, url: server.url, header_names: [] as string[] };
  // "Test" reconnects, because the point of the button is what is true NOW.
  // Filling the list on page load must not: it would drop the connection the
  // chat is using and re-handshake every server on every settings render.
  if (reconnect) resetMcpTools();
  const { entries, errors } = await mcpTools([server]);
  if (errors[id]) return { ...base, ok: false, tools: [], error: errors[id] };

  return {
    ...base,
    ok: true,
    tools: entries.map((entry) => ({
      name: entry.name,
      description: (entry.tool.description ?? "").trim().split("\n")[0].slice(0, 200),
      app: toolAppUri(entry.tool) ?? null,
    })),
  };
}
