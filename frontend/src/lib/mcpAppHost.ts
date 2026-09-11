/**
 * The host half of MCP Apps (SEP-1865), for one tool call that ships a UI.
 *
 * Built on `@modelcontextprotocol/ext-apps`, the extension's official SDK. Its
 * `AppBridge` is the host end of the protocol and `PostMessageTransport` carries
 * JSON-RPC to the iframe, so the wire format, version negotiation and the
 * ordering rules are the SDK's problem rather than ours.
 *
 * WHY THE SDK RATHER THAN OUR OWN. This was hand-written first, and a real
 * third-party app found three conformance bugs our own tests could not, because
 * our demo server happened to avoid every one of them: a `hostContext.toolInfo`
 * missing the required `inputSchema`, app-only tools handed to the model, and a
 * display-mode request declined because we never negotiated. Each was a rule we
 * had read and still got wrong. The SDK encodes them once.
 *
 * WHAT REMAINS OURS, and why it cannot be the SDK's. `AppBridge` takes an MCP
 * `Client` to forward the app's `tools/call` and `resources/read` to. We have no
 * client in the browser: the app's iframe has an opaque origin and no network,
 * and the only MCP connection lives in the deployment. So the client is `null`
 * and those two arrive as handlers, answered through `POST /mcp/call` and
 * `POST /mcp/resource`, where the same-server and open-to-apps rules are
 * enforced. A view is server-authored HTML; nothing it sends is trusted.
 *
 * ONE DEVIATION, DELIBERATE. SEP-1865 says a web host MUST wrap the view in a
 * different-origin sandbox proxy, so a view can hold `allow-same-origin`
 * without holding the host's origin. We serve the SPA from one origin and have
 * nowhere to put a second, so we render the view directly with `allow-scripts`
 * and never `allow-same-origin`. Stricter than the proxy it replaces, but an app
 * needing same-origin will not run here.
 */
import { AppBridge, PostMessageTransport } from "@modelcontextprotocol/ext-apps/app-bridge";

/** A tool result as the host hands it to a view. */
export interface McpToolResult {
  structuredContent?: unknown;
  content?: unknown[];
}

export interface McpAppHostConfig {
  /** The tool whose app this is. Its name scopes every call the view makes. */
  toolName: string;
  /**
   * Its JSON Schema, for `hostContext.toolInfo.tool`.
   *
   * `Tool` declares `inputSchema` as required and the SDK validates the
   * initialize result, so omitting it is not a cautious partial answer: an app
   * built on that SDK rejects the handshake outright.
   */
  toolInputSchema?: Record<string, unknown>;
  /** Proxy a `tools/call` the view made. Rejects with the reason on refusal. */
  onToolCall?: (name: string, args: Record<string, unknown>) => Promise<McpToolResult>;
  /** Read a resource for the view, since an origin-less frame cannot fetch. */
  onReadResource?: (uri: string) => Promise<unknown[]>;
  /** Put the view's text into the conversation (`ui/message`). */
  onMessage?: (text: string) => void;
  /** Replace the context the view contributes to the next turn. */
  onModelContext?: (context: Record<string, unknown>) => void;
  /** The view's reported content height, in pixels. */
  onHeight: (height: number) => void;
  /** Display modes this surface can put the view into. Inline only by default. */
  displayModes?: string[];
  /** The view got a new display mode. Move it, then the host notifies it. */
  onDisplayMode?: (mode: string) => void;
}

export interface McpAppHost {
  /** Attach to a live iframe and run the handshake. */
  connect: (view: Window) => Promise<void>;
  /** The tool's arguments as they stand. `final` closes the partial stream. */
  setToolInput: (args: Record<string, unknown>, final: boolean) => void;
  /** The finished result. */
  setToolResult: (result: McpToolResult) => void;
  /** Tell the view its mode changed, when the HOST is the one changing it. */
  setDisplayMode: (mode: string) => void;
  /** Ask the view to shut down before the frame is dropped. */
  teardown: (reason: string) => void;
}

/**
 * The subset of the standardized theme variables we can answer honestly.
 *
 * Keys are from the Theming section of SEP-1865; values are the SPA tokens they
 * come from. Only variables we actually have are sent: the spec has views fall
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
  const supported = config.displayModes ?? ["inline"];
  let mode = supported.includes("inline") ? "inline" : supported[0];

  const hostContext = {
    // A COMPLETE `Tool`. `{type: "object"}` only when the server published no
    // schema at all, which is a valid empty object schema rather than an
    // invention: the alternative is a handshake the app refuses.
    toolInfo: {
      tool: {
        name: config.toolName,
        inputSchema: config.toolInputSchema ?? { type: "object" },
      },
    },
    theme: document.documentElement.classList.contains("dark") ? "dark" : "light",
    styles: { variables: themeVariables() },
    displayMode: mode,
    availableDisplayModes: supported,
    // Flexible height: the view decides, up to a ceiling, and tells us through
    // `ui/notifications/size-changed`.
    containerDimensions: { maxHeight: 640 },
    locale: navigator.language,
    timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    userAgent: "custom-demos-spa",
    platform: "web",
  };

  const bridge = new AppBridge(
    // No MCP client: the browser has none, so the two proxied methods are
    // answered by the handlers below instead of being forwarded by the SDK.
    null,
    { name: "custom-demos-spa", version: "1.0.0" },
    {
      openLinks: {},
      logging: {},
      ...(config.onToolCall ? { serverTools: {} } : {}),
      ...(config.onReadResource ? { serverResources: {} } : {}),
    },
    { hostContext: hostContext as never },
  );

  bridge.onsizechange = ({ height }) => {
    if (typeof height === "number") config.onHeight(height);
  };

  bridge.oncalltool = async (params) => {
    if (!config.onToolCall) throw new Error("This host does not proxy tool calls from an app.");
    const result = await config.onToolCall(
      String(params.name),
      (params.arguments ?? {}) as Record<string, unknown>,
    );
    return {
      content: (result.content ?? []) as never,
      structuredContent: result.structuredContent as never,
      isError: false,
    };
  };

  bridge.onreadresource = async (params) => {
    if (!config.onReadResource) throw new Error("This host does not proxy resources/read.");
    return { contents: (await config.onReadResource(String(params.uri))) as never };
  };

  bridge.onopenlink = async (params) => {
    const url = String(params.url ?? "");
    // Only the schemes a link can safely be. `javascript:` in particular would
    // run in THIS page, which is the whole thing the sandbox prevents.
    if (!/^https?:\/\//i.test(url)) throw new Error("Only http and https links can be opened.");
    window.open(url, "_blank", "noopener,noreferrer");
    return {};
  };

  bridge.onrequestdisplaymode = async (params) => {
    const wanted = String(params.mode ?? "");
    // The SDK already refuses a mode the view never declared. This is the other
    // half: a mode THIS surface cannot do. Either way the resulting mode is
    // returned, which is what lets a view rely on the answer.
    if (supported.includes(wanted) && wanted !== mode) {
      mode = wanted;
      config.onDisplayMode?.(mode);
    }

    return { mode: mode as "inline" | "fullscreen" | "pip" };
  };

  bridge.onupdatemodelcontext = async (params) => {
    if (!config.onModelContext) throw new Error("This host does not carry model context.");
    // Each call REPLACES the last, per the spec, so the handler is handed the
    // whole thing rather than a delta to merge.
    config.onModelContext({
      content: params.content,
      structuredContent: params.structuredContent,
    });
    return {};
  };

  bridge.onmessage = async (params) => {
    // The honest refusal. An app rendered for a PAUSED call cannot put a turn
    // into the conversation, and saying so beats accepting and dropping it.
    if (!config.onMessage) throw new Error("This host cannot accept a message right now.");
    const text = String((params.content as { text?: string })?.text ?? "").trim();
    if (!text) throw new Error("ui/message needs content.text.");
    config.onMessage(text);
    return {};
  };

  return {
    connect: async (view: Window) => {
      // Host end: post to the frame, accept only from the frame. A sandbox with
      // no allow-same-origin has an opaque origin, so matching the source window
      // is the only identification available, and the SDK does it for us.
      await bridge.connect(new PostMessageTransport(view, view));
    },
    setToolInput: (args, final) => {
      // The SDK enforces the ordering the spec fixes: partials before the one
      // `tool-input`, and none after it.
      void (final
        ? bridge.sendToolInput({ arguments: args })
        : bridge.sendToolInputPartial({ arguments: args }));
    },
    setToolResult: (result) => {
      void bridge.sendToolResult({
        content: (result.content ?? []) as never,
        structuredContent: result.structuredContent as never,
      });
    },
    setDisplayMode: (next: string) => {
      if (!supported.includes(next) || next === mode) return;
      mode = next;
      config.onDisplayMode?.(next);
      void bridge.sendHostContextChange({ displayMode: next as "inline" | "fullscreen" | "pip" });
    },
    teardown: (reason: string) => {
      // A request, not a notification: the spec has the host wait for the view
      // to acknowledge so it can save what the person typed. We do not block the
      // unmount on it, but sending it is what gives a view the chance.
      void bridge.teardownResource({ reason }).catch(() => {});
    },
  };
}
