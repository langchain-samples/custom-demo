/**
 * The host half of MCP Apps (SEP-1865), for one tool call that ships a UI.
 *
 * A server can ship a `ui://` HTML resource bound to a tool, and a host that
 * understands the extension renders it in a sandboxed iframe and talks to it in
 * JSON-RPC 2.0 over `postMessage`. The View acts as an MCP client; we are the
 * server it connects to, proxying to the real one. This module is that server,
 * kept out of the card so the React component stays about layout.
 *
 * The sequence, all of it standard:
 *
 *   View -> Host   ui/initialize                  answered with McpUiInitializeResult
 *   View -> Host   ui/notifications/initialized
 *   Host -> View   ui/notifications/tool-input    the arguments the tool was called with
 *   Host -> View   ui/notifications/tool-result   the result of this leg of the call
 *   View -> Host   ui/notifications/size-changed  the content resized
 *   View -> Host   tools/call, resources/read     proxied to the app's server
 *   Host -> View   ui/resource-teardown           before the frame goes away
 *
 * The tool finishes first, and its result is what the app draws. When a person
 * does something in the app it submits by CALLING A TOOL, normally one the
 * server marked `visibility: ["app"]` so the model cannot call it. The host
 * proxies that call, which is what SEP-1865 asks of it; `POST /mcp/call` is
 * where the same-server and open-to-apps rules are enforced, because a view is
 * server-authored HTML and nothing it sends is trusted.
 *
 * There is no pause in any of this. Elicitation is a different extension for a
 * different problem, and an MCP App does not need it.
 *
 * ONE DEVIATION, DELIBERATE. SEP-1865 says a web host MUST wrap the View in a
 * different-origin sandbox proxy, so that the View can hold `allow-same-origin`
 * without holding the host's origin. We serve the SPA from a single origin and
 * have nowhere to put that second one, so we render the View directly with
 * `allow-scripts` and never `allow-same-origin`. That is stricter than the
 * proxy it replaces, not looser: the frame has an opaque origin and no route to
 * this page's storage. The cost is that a third-party app needing
 * `allow-same-origin` will not work here.
 */
/** A tool result as the host hands it to a view. */
export interface McpToolResult {
  structuredContent?: unknown;
  content?: unknown[];
}

/** The MCP Apps protocol revision this host implements. */
export const PROTOCOL_VERSION = "2026-01-26";

/** A JSON-RPC 2.0 message, as far as this bridge needs to read one. */
interface RpcMessage {
  jsonrpc?: string;
  id?: string | number;
  method?: string;
  params?: Record<string, unknown>;
}

export interface McpAppHostConfig {
  /** The tool whose app this is. Its name scopes every call the view makes. */
  toolName: string;
  /** The arguments it was called with, replayed to the View. */
  toolArguments: Record<string, unknown>;
  /** Its result, which is the data the app draws. */
  toolResult: McpToolResult;
  /**
   * Its JSON Schema, for `hostContext.toolInfo.tool`.
   *
   * `Tool` declares `inputSchema` as required, and the official app SDK
   * validates the initialize result, so omitting it is not a modest partial
   * answer: an app built on that SDK rejects the handshake outright. Excalidraw
   * reports it as `path: ["hostContext","toolInfo","tool","inputSchema"]`.
   */
  toolInputSchema?: Record<string, unknown>;
  /** Proxy a `tools/call` the view made. Rejects with the reason on refusal. */
  onToolCall?: (name: string, args: Record<string, unknown>) => Promise<McpToolResult>;
  /** The View's reported content height, in pixels. */
  onHeight: (height: number) => void;
  /**
   * Read a resource for the View (`resources/read`).
   *
   * Optional because the capability is advertised only when it is wired: an
   * origin-less iframe cannot fetch, so a View that needs data has no route but
   * this one, and a host that claims the capability and then refuses is worse
   * than one that never claimed it.
   */
  onReadResource?: (uri: string) => Promise<unknown[]>;
  /** Put the View's text into the conversation (`ui/message`). */
  onMessage?: (text: string) => void;
  /** Replace the context the View contributes to the next turn. */
  onModelContext?: (context: Record<string, unknown>) => void;
}

export interface McpAppHost {
  /** Feed every window message here. Anything not from `view` is ignored. */
  handleMessage: (event: MessageEvent, view: Window | null) => void;
  /** Ask the View to shut down before the frame is dropped. */
  teardown: (view: Window | null, reason: string) => void;
}

/**
 * The subset of the standardized theme variables we can answer honestly.
 *
 * Keys are from the Theming section of SEP-1865; values are the SPA tokens they
 * come from. Only variables we actually have are sent: the spec has Views fall
 * back to their own defaults for anything omitted, so a partial set degrades
 * cleanly and an invented one would not.
 *
 * `inverse` is the odd one. The standardized set has no brand or accent token,
 * and inverse is where a high-contrast primary action surface belongs, so the
 * brand colour goes there and an app's primary button picks it up.
 */
const THEME_VARIABLES: Record<string, string> = {
  "--color-background-primary": "--background",
  "--color-background-secondary": "--panel",
  "--color-background-tertiary": "--panel-2",
  "--color-text-primary": "--foreground",
  "--color-text-secondary": "--muted-foreground",
  "--color-border-primary": "--border",
  "--color-background-inverse": "--brand-primary",
  "--color-text-inverse": "--brand-fg",
  "--border-radius-md": "--radius-md",
};

/** The host's theme, read off the live document rather than guessed. */
function themeVariables(): Record<string, string> {
  const styles = getComputedStyle(document.documentElement);
  const out: Record<string, string> = {};
  for (const [standard, token] of Object.entries(THEME_VARIABLES)) {
    const value = styles.getPropertyValue(token).trim();
    if (value) out[standard] = value;
  }

  const font = getComputedStyle(document.body).fontFamily;
  if (font) out["--font-sans"] = font;
  return out;
}

/** An MCP Apps host bound to one tool call. */
export function createMcpAppHost(config: McpAppHostConfig): McpAppHost {

  const post = (view: Window | null, message: Record<string, unknown>) => {
    view?.postMessage({ jsonrpc: "2.0", ...message }, "*");
  };

  const notify = (view: Window | null, method: string, params: Record<string, unknown>) => {
    post(view, { method, params });
  };

  const reply = (view: Window | null, id: string | number, result: Record<string, unknown>) => {
    post(view, { id, result });
  };

  const fail = (view: Window | null, id: string | number, message: string) => {
    post(view, { id, error: { code: -32000, message } });
  };

  /** The initialize result: who we are, what we support, and the host's look. */
  const initializeResult = () => ({
    protocolVersion: PROTOCOL_VERSION,
    hostInfo: { name: "custom-demos-spa", version: "1.0.0" },
    hostCapabilities: {
      openLinks: {},
      serverTools: {},
      logging: {},
      // Advertised only when the caller wired a reader. The spec has Views check
      // capabilities before relying on a method, so claiming this without one
      // would send an app down a path that can only fail.
      ...(config.onReadResource ? { serverResources: {} } : {}),
    },
    hostContext: {
      // A complete `Tool`. `{type: "object"}` only when the server published no
      // schema at all, which is still a valid empty object schema rather than an
      // invention: the alternative is a handshake the app refuses.
      toolInfo: {
        tool: {
          name: config.toolName,
          inputSchema: config.toolInputSchema ?? { type: "object" },
        },
      },
      theme: document.documentElement.classList.contains("dark") ? "dark" : "light",
      styles: { variables: themeVariables() },
      displayMode: "inline",
      availableDisplayModes: ["inline"],
      // Flexible height: the View decides, up to a ceiling, and tells us through
      // `ui/notifications/size-changed`.
      containerDimensions: { maxHeight: 640 },
      locale: navigator.language,
      timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      userAgent: "custom-demos-spa",
      platform: "web",
    },
  });

  /** Hand the View the call it is rendering, in the order the spec requires. */
  const sendCall = (view: Window | null) => {
    notify(view, "ui/notifications/tool-input", { arguments: config.toolArguments });
    notify(view, "ui/notifications/tool-result", {
      structuredContent: config.toolResult.structuredContent ?? {},
      content: config.toolResult.content ?? [],
    });
  };

  const onToolsCall = (
    view: Window | null,
    id: string | number,
    params: Record<string, unknown>,
  ) => {
    const name = String(params.name ?? "");
    if (!config.onToolCall) {
      fail(view, id, "This host does not proxy tool calls from an app.");
      return;
    }
    if (!name) {
      fail(view, id, "tools/call needs a name.");
      return;
    }

    const args = (params.arguments ?? {}) as Record<string, unknown>;
    // Async, so the reply comes later. The refusal path answers too: the app is
    // holding a promise open and a dropped request is a button that hangs.
    void config
      .onToolCall(name, args)
      .then((result) => reply(view, id, { ...result, isError: false }))
      .catch((err: unknown) =>
        fail(view, id, err instanceof Error ? err.message : String(err)),
      );
  };

  const handleMessage = (event: MessageEvent, view: Window | null) => {
    // A sandbox without allow-same-origin has an opaque origin, so every message
    // arrives as "null" and origin cannot identify it. Match the frame's own
    // window, which no other document can forge.
    if (!view || event.source !== view) return;
    const msg = (event.data ?? {}) as RpcMessage;
    if (msg.jsonrpc !== "2.0" || !msg.method) return;
    const params = msg.params ?? {};

    if (msg.id === undefined) {
      if (msg.method === "ui/notifications/initialized") sendCall(view);
      else if (msg.method === "ui/notifications/size-changed") {
        const height = params.height;
        if (typeof height === "number") config.onHeight(height);
      }
      // `notifications/message` is the View's log channel. Nothing to do with it
      // here, and a notification takes no reply.
      return;
    }

    switch (msg.method) {
      case "ui/initialize":
        reply(view, msg.id, initializeResult());
        return;
      case "tools/call":
        onToolsCall(view, msg.id, params);
        return;
      case "ui/open-link": {
        const url = String(params.url ?? "");
        // Only the schemes a link can safely be. `javascript:` in particular
        // would run in THIS page, which is the whole thing the sandbox prevents.
        if (!/^https?:\/\//i.test(url)) {
          fail(view, msg.id, "Only http and https links can be opened.");
          return;
        }

        window.open(url, "_blank", "noopener,noreferrer");
        reply(view, msg.id, {});
        return;
      }
      case "ui/request-display-mode":
        // Inline is the only mode this card has room for, and the spec wants the
        // resulting mode returned whether or not it changed.
        reply(view, msg.id, { mode: "inline" });
        return;
      case "resources/read": {
        const uri = String(params.uri ?? "");
        if (!config.onReadResource) {
          fail(view, msg.id, "This host does not proxy resources/read.");
          return;
        }
        if (!uri) {
          fail(view, msg.id, "resources/read needs a uri.");
          return;
        }

        // Async, so the reply comes later. The View is holding a promise open
        // and a dropped request would hang it, which is why the rejection path
        // answers too.
        void config
          .onReadResource(uri)
          .then((contents) => reply(view, msg.id as string | number, { contents }))
          .catch((err: unknown) =>
            fail(view, msg.id as string | number, err instanceof Error ? err.message : String(err)),
          );
        return;
      }
      case "ui/message": {
        const content = params.content as { text?: string } | undefined;
        const text = String(content?.text ?? "").trim();
        if (!config.onMessage) {
          // The honest refusal. An app rendered for a PAUSED tool call cannot
          // put a turn into the conversation: the run is interrupted and the
          // only thing that moves it is the elicitation answer. Saying so beats
          // accepting the message and dropping it.
          fail(view, msg.id, "This host cannot accept a message while a tool call is paused.");
          return;
        }
        if (!text) {
          fail(view, msg.id, "ui/message needs content.text.");
          return;
        }

        config.onMessage(text);
        reply(view, msg.id, {});
        return;
      }
      case "ui/update-model-context":
        if (!config.onModelContext) {
          fail(view, msg.id, "This host does not carry model context from an app.");
          return;
        }

        // Each call REPLACES the last, per the spec, so the handler is handed
        // the whole thing rather than a delta to merge.
        config.onModelContext({
          content: params.content,
          structuredContent: params.structuredContent,
        });
        reply(view, msg.id, {});
        return;
      case "ping":
        reply(view, msg.id, {});
        return;
      default:
        fail(view, msg.id, `Unsupported method: ${msg.method}`);
    }
  };

  const teardown = (view: Window | null, reason: string) => {
    // A request, not a notification: the spec has the host wait for the View to
    // acknowledge so it can save what the person typed. We do not block the
    // unmount on it, but sending it is what gives a View the chance.
    post(view, { id: `teardown-${Date.now()}`, method: "ui/resource-teardown", params: { reason } });
  };

  return { handleMessage, teardown };
}
