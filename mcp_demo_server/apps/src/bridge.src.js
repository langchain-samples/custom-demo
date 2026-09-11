/**
 * The MCP Apps client every app in this server shares, on the official SDK.
 *
 * Source. The file the server actually inlines is `apps/bridge.js`, built from
 * this by `apps/build.sh`. An app is served as a `ui://` resource and rendered
 * in a sandboxed iframe with an opaque origin, so it can fetch nothing: the SDK
 * has to be bundled into the document rather than loaded from anywhere.
 *
 * `App` from `@modelcontextprotocol/ext-apps` is the view end of SEP-1865. It
 * owns the handshake, the JSON-RPC framing, version negotiation and the
 * ordering rules, and `autoResize` reports our height through a ResizeObserver
 * without us asking. What is left here is a four-method surface the app files
 * use, so a new app is markup and logic only.
 *
 * WHY THE SDK. Hand-writing this worked and was wrong in a way nothing caught:
 * it sent `clientInfo` where `ui/initialize` requires `appInfo`. Our host was
 * hand-written too, so the two agreed with each other and neither agreed with
 * the spec, right until a conformant host refused the handshake.
 *
 *   McpApp.onInit(function (result) { ...draw from result.data... });
 *   McpApp.call("submit_thing", { ...what the person chose... });
 *   McpApp.ready();
 */
import { App } from "@modelcontextprotocol/ext-apps";

const app = new App(
  { name: "meridian-app", version: "1.0.0" },
  { availableDisplayModes: ["inline"] },
);

/** The app's renderer, registered through `onInit`. */
let render = null;
/** Whether the app has finished wiring its DOM and called `ready`. */
let wired = false;
/** The data to draw, once the tool result has brought it. */
let result = null;
/** Whether `render` has already been handed the result. */
let rendered = false;
/** The arguments the tool was called with, replayed back with a submission. */
let toolArguments = {};

/**
 * Hand the result to the app, once both halves are ready.
 *
 * The two arrive in either order: the host sends as soon as the handshake
 * finishes, while the app calls `ready` when its own DOM is wired. Whichever
 * lands second triggers the render, and it only happens once.
 */
function maybeRender() {
  if (rendered || !wired || !render || !result) return;
  rendered = true;
  try {
    render(result, app.getHostContext() || {});
  } catch (err) {
    // A broken app must still let the person out, or the run is stuck with no
    // way to answer it.
    console.error("mcp app failed to render", err);
  }
}

app.ontoolinput = (params) => {
  toolArguments = params.arguments || {};
};

/**
 * The finished result, which is the data the app draws.
 *
 * `structuredContent` is the shaped half and what an app should read; `content`
 * is the block list the model sees. Both are handed over, because a server may
 * fill one and not the other.
 */
app.ontoolresult = (params) => {
  result = { data: params.structuredContent || {}, content: params.content || [] };
  maybeRender();
};

/** Partial arguments while the model is still writing them. */
app.ontoolinputpartial = (params) => {
  toolArguments = params.arguments || {};
};

/**
 * Adopt the host's palette.
 *
 * `styles.variables` is the standardized set from the Theming section of
 * SEP-1865. They go on the root and shell.css reads them through
 * `var(..., <fallback>)`, so a host that sends none still gets a coherent app.
 */
function applyHostContext(context) {
  const root = document.documentElement;
  if (context.theme === "dark") root.setAttribute("data-theme", "dark");
  else if (context.theme === "light") root.removeAttribute("data-theme");

  const styles = context.styles || {};
  for (const [name, value] of Object.entries(styles.variables || {})) {
    if (value) root.style.setProperty(name, value);
  }

  // A host may ship @font-face rules for its own typeface. They have to be
  // injected as CSS: the frame has no origin and cannot fetch a stylesheet.
  const fonts = (styles.css || {}).fonts;
  if (fonts) {
    const tag = document.createElement("style");
    tag.textContent = fonts;
    document.head.appendChild(tag);
  }
}

app.onhostcontextchanged = (params) => applyHostContext(params || {});

window.McpApp = {
  /** Register the renderer. Called once, when the host sends the result. */
  onInit(fn) {
    render = fn;
    maybeRender();
  },
  /**
   * Call a tool on this app's own server.
   *
   * How a result-bound app submits: the server publishes a tool marked
   * `visibility: ["app"]`, invisible to the model, and the app calls it. The
   * host proxies it, which is what SEP-1865 asks a host to do.
   */
  call(name, args) {
    return app.callServerTool({ name, arguments: args || {} });
  },
  /** Read a resource from this app's own server. */
  read(uri) {
    return app.readServerResource({ uri });
  },
  /** Ask the host to open a link, which a sandboxed frame cannot do itself. */
  openLink(url) {
    return app.openLink({ url });
  },
  /** The arguments the tool was called with. */
  args() {
    return toolArguments;
  },
  /**
   * The app's DOM is wired and it can be handed the result.
   *
   * Sizing needs no call: `autoResize` is on by default, so the SDK reports
   * height through a ResizeObserver.
   */
  ready() {
    wired = true;
    maybeRender();
  },
  /** Kept for apps that force a re-measure; the SDK observes size itself. */
  resize() {},
};

// The handshake opens as soon as this runs, before the app's markup has parsed.
// Deliberate: host context (theme, sizing) should be applied before anything
// paints, and the result can wait for `ready`.
app
  .connect()
  .then(() => applyHostContext(app.getHostContext() || {}))
  .catch((err) => console.error("mcp app failed to initialize", err));
