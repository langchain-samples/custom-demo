/**
 * The host half of MCP Apps (SEP-1865), for one paused tool call.
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
 *   View -> Host   tools/call                     the answer, as a fresh call
 *   Host -> View   ui/resource-teardown           before the frame goes away
 *
 * WHY A TOOL RESULT CARRIES A QUESTION. Our tools pause. Under SEP-2322 a tool
 * that needs input returns an `InputRequiredResult` naming what it wants, and
 * the client re-calls the same tool with `inputResponses` attached. That is an
 * ordinary tool result and an ordinary tool call, so composing the two SEPs
 * needs no message of our own, and an app written against either one works
 * here. We hand the answer to LangGraph rather than to the MCP server directly,
 * because the paused call belongs to the agent's run: proxying a View's
 * `tools/call` on to the server is exactly what SEP-1865 asks a host to do, and
 * for us the route there runs through resuming the interrupt.
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
import type { JsonSchema, McpElicitationRequest, McpElicitationResponse } from "@/lib/api";

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
  /** The paused tool, whose name the View must use when it calls back. */
  toolName: string;
  /** The arguments it was called with, replayed to the View and back to us. */
  toolArguments: Record<string, unknown>;
  /** The question this leg of the call returned. */
  request: McpElicitationRequest;
  /** The View's answer, ready to resume the run with. */
  onAnswer: (response: McpElicitationResponse) => void;
  /** The View's reported content height, in pixels. */
  onHeight: (height: number) => void;
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

/**
 * The question, in the shape a tool result carries it on the wire.
 *
 * `inputRequests` keyed by the server's own key, each an `elicitation/create`
 * request, is the SEP-2322 form. camelCase because that is the wire spelling;
 * `langchain.mcp` hands us the snake_case one, so this converts back.
 */
function inputRequiredResult(request: McpElicitationRequest) {
  return {
    resultType: "input_required",
    inputRequests: {
      [request.key]: {
        method: "elicitation/create",
        params: {
          mode: "form",
          message: request.message,
          requestedSchema: (request.requested_schema ?? {}) as JsonSchema,
        },
      },
    },
  };
}

/** Read the answer out of a View's `tools/call`, or null if it carries none. */
function answerFrom(
  params: Record<string, unknown>,
  key: string,
): McpElicitationResponse | null {
  const responses = (params.inputResponses ?? params.input_responses) as
    | Record<string, McpElicitationResponse>
    | undefined;
  if (!responses) return null;
  // Keyed by the server's own key, but an app that answers the only question it
  // was given without echoing the key is answering unambiguously, so take the
  // single entry rather than failing the resume over a spelling.
  const answer = responses[key] ?? Object.values(responses)[0];
  return answer && typeof answer === "object" ? answer : null;
}

/** An MCP Apps host bound to one paused tool call. */
export function createMcpAppHost(config: McpAppHostConfig): McpAppHost {
  let answered = false;

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
    },
    hostContext: {
      // Only the name. We know which tool paused, but not its declared schema,
      // and a made-up one would be worse than an absent one.
      toolInfo: { tool: { name: config.toolName } },
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
    notify(view, "ui/notifications/tool-result", inputRequiredResult(config.request));
  };

  const onToolsCall = (view: Window | null, id: string | number, params: Record<string, unknown>) => {
    if (params.name && params.name !== config.toolName) {
      // The spec lets a View call any app-visible tool on its own server, which
      // for us would mean starting a second call while this one is paused. Say
      // so rather than dropping it: a silent no-op leaves the app waiting on a
      // promise that never settles.
      fail(view, id, `This host only proxies calls to the paused tool (${config.toolName}).`);
      return;
    }

    const answer = answerFrom(params, config.request.key);
    if (!answer) {
      fail(view, id, "A tools/call from an app must carry inputResponses for the paused question.");
      return;
    }
    if (answered) {
      fail(view, id, "This tool call has already been answered.");
      return;
    }

    answered = true;
    config.onAnswer(answer);
    // The real result belongs to the agent's next step, which outlives this
    // frame, so acknowledge the handoff rather than inventing tool output.
    reply(view, id, {
      content: [{ type: "text", text: "Answer accepted. The paused tool call is resuming." }],
    });
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
