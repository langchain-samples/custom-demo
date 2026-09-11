/**
 * The MCP Apps client every app in this server shares.
 *
 * Each app is HTML served as a `ui://` resource and rendered by the host inside
 * a sandboxed iframe. The wire protocol is SEP-1865 (MCP Apps, stable as of
 * 2026-01-26): JSON-RPC 2.0 over `postMessage`, with the View acting as an MCP
 * client and the host as the server that proxies to the real one. It is the
 * same for all four apps, so it lives here and is injected into each of them at
 * serve time (see apps.py) rather than copy-pasted.
 *
 * The sequence, all of it standard:
 *
 *   View -> Host   ui/initialize                  capabilities and protocol version
 *   Host -> View   McpUiInitializeResult          hostContext: theme, styles, toolInfo
 *   View -> Host   ui/notifications/initialized
 *   Host -> View   ui/notifications/tool-input    the arguments the tool was called with
 *   Host -> View   ui/notifications/tool-result   the result of this leg of the call
 *   View -> Host   ui/notifications/size-changed  whenever the content resizes
 *   View -> Host   tools/call                     the answer, as a fresh call
 *
 * WHY A TOOL RESULT CARRIES A QUESTION. These tools pause: under SEP-2322 a tool
 * that needs input returns an `InputRequiredResult` naming what it wants, and
 * the client re-calls the same tool with `inputResponses` attached. That is an
 * ordinary tool result, so it reaches the View through the ordinary
 * `ui/notifications/tool-result`, and the answer goes back through the ordinary
 * `tools/call`. Composing the two SEPs needs no message of our own, which is the
 * point: an app written against this file is an app any MCP Apps host can run.
 *
 * An app uses it like this, and never sees the JSON-RPC:
 *
 *   McpApp.onInit(function (request) { ...render from request... });
 *   McpApp.submit({ approved: true });
 *   McpApp.ready();
 *
 * Everything is inline and dependency-free: the iframe has no origin, so there
 * is nowhere to load a script from.
 */
window.McpApp = (function () {
  "use strict";

  /** The MCP Apps protocol revision this file implements. */
  var PROTOCOL_VERSION = "2026-01-26";

  /** JSON-RPC id for the next request out. Ids only have to be unique per peer. */
  var nextId = 1;
  /** In-flight requests we sent, by id, awaiting a response. */
  var pending = {};
  /** Handlers for notifications the host sends us, by method. */
  var onNotify = {};
  /** Handlers for requests the host sends us, by method. Each returns a result. */
  var onRequest = {};

  /** The app's renderer, registered through `onInit`. */
  var render = null;
  /** Whether the app has finished wiring its DOM and called `ready`. */
  var wired = false;
  /** The question to render, once `ui/notifications/tool-result` has brought it. */
  var question = null;
  /** Whether `render` has already been handed the question. */
  var rendered = false;

  /** The paused tool's name, from `hostContext.toolInfo`. */
  var toolName = null;
  /** The arguments it was called with, from `ui/notifications/tool-input`. */
  var toolArguments = {};
  /** Theme, sizing and tool metadata the host sent, merged as updates arrive. */
  var hostContext = {};

  /** Last size reported to the host, so an unchanged one is not re-sent. */
  var lastWidth = 0;
  var lastHeight = 0;

  function post(message) {
    // The parent frame is the host, which knows which call this iframe belongs
    // to. A sandboxed frame has an opaque origin, so "*" is the only target that
    // works; the host authenticates us by matching `event.source` instead.
    parent.postMessage(message, "*");
  }

  /** Send a JSON-RPC request and resolve when the host answers it. */
  function request(method, params) {
    var id = nextId++;
    post({ jsonrpc: "2.0", id: id, method: method, params: params || {} });
    return new Promise(function (resolve, reject) {
      pending[id] = { resolve: resolve, reject: reject };
    });
  }

  /** Send a JSON-RPC notification, which by definition has no id and no reply. */
  function notify(method, params) {
    post({ jsonrpc: "2.0", method: method, params: params || {} });
  }

  window.addEventListener("message", function (event) {
    var msg = event.data;
    if (!msg || msg.jsonrpc !== "2.0") return;

    // A response to something we sent.
    if (msg.method === undefined && msg.id !== undefined) {
      var slot = pending[msg.id];
      if (!slot) return;
      delete pending[msg.id];
      if (msg.error) slot.reject(new Error(msg.error.message || "host error"));
      else slot.resolve(msg.result);
      return;
    }

    // A notification from the host.
    if (msg.id === undefined) {
      var handler = onNotify[msg.method];
      if (handler) handler(msg.params || {});
      return;
    }

    // A request from the host, which owes a response even when we do nothing.
    var responder = onRequest[msg.method];
    var result = responder ? responder(msg.params || {}) : {};
    post({ jsonrpc: "2.0", id: msg.id, result: result || {} });
  });

  /**
   * Adopt the host's look, so the app reads as part of the host's page.
   *
   * `styles.variables` is the standardized set of CSS custom properties from the
   * Theming section of SEP-1865. They are set on the root and shell.css consumes
   * them through `var(..., <fallback>)`, so a host that sends none, or only
   * some, still gets a coherent app.
   */
  function applyHostContext(context) {
    hostContext = context || {};
    var root = document.documentElement;

    if (hostContext.theme === "dark") root.setAttribute("data-theme", "dark");
    else if (hostContext.theme === "light") root.removeAttribute("data-theme");

    var styles = hostContext.styles || {};
    var variables = styles.variables || {};
    for (var name in variables) {
      if (variables[name]) root.style.setProperty(name, variables[name]);
    }

    // The host may ship @font-face rules for its own typeface. They have to be
    // injected as CSS, because the frame has no origin and cannot fetch a
    // stylesheet.
    var fonts = (styles.css || {}).fonts;
    if (fonts) {
      var tag = document.createElement("style");
      tag.textContent = fonts;
      document.head.appendChild(tag);
    }

    var tool = (hostContext.toolInfo || {}).tool;
    if (tool && tool.name) toolName = tool.name;

    applyContainerDimensions(hostContext.containerDimensions);
  }

  /**
   * Honour the space the host has given us.
   *
   * A fixed `height` means the host controls the box and the app should fill it;
   * `maxHeight` means the app controls its own height up to a ceiling, which is
   * the case that pairs with `ui/notifications/size-changed` below.
   */
  function applyContainerDimensions(dimensions) {
    if (!dimensions) return;
    var root = document.documentElement.style;
    if (typeof dimensions.height === "number") root.height = "100vh";
    else if (typeof dimensions.maxHeight === "number") root.maxHeight = dimensions.maxHeight + "px";

    if (typeof dimensions.width === "number") root.width = "100vw";
    else if (typeof dimensions.maxWidth === "number") root.maxWidth = dimensions.maxWidth + "px";
  }

  /**
   * Tell the host how big we are.
   *
   * An app that grows (a chart appearing, a validation message) would otherwise
   * be clipped by the iframe it sits in. Skipped when nothing moved, so dragging
   * a slider does not flood the host with notifications.
   */
  function reportSize() {
    if (!document.body) return;
    var width = document.body.scrollWidth;
    var height = document.body.scrollHeight + 4;
    if (Math.abs(height - lastHeight) < 2 && Math.abs(width - lastWidth) < 2) return;
    lastWidth = width;
    lastHeight = height;
    notify("ui/notifications/size-changed", { width: width, height: height });
  }

  /**
   * Hand the question to the app, once both halves are ready.
   *
   * The two arrive in either order: the host sends the result as soon as the
   * handshake finishes, while the app calls `ready` when its own DOM is wired.
   * Whichever lands second triggers the render, and it only ever happens once.
   */
  function maybeRender() {
    if (rendered || !wired || !render || !question) return;
    rendered = true;
    try {
      render(question, hostContext);
    } catch (err) {
      // A broken app must still let the person out of the pause, or the run is
      // stuck with no way to answer it.
      console.error("mcp app failed to render", err);
    }
    reportSize();
  }

  onNotify["ui/notifications/tool-input"] = function (params) {
    toolArguments = params.arguments || {};
  };

  /**
   * The result of this leg of the call, which for a pausing tool is the question.
   *
   * `inputRequests` is the SEP-2322 wire shape: one entry per question, keyed by
   * the server's own key, each an `elicitation/create` request. The key has to
   * travel back with the answer, so it is kept beside the params. Only the first
   * is rendered: an app is bound to one tool and answers one question, and a
   * host with no app falls back to a generated form for the rest.
   */
  onNotify["ui/notifications/tool-result"] = function (params) {
    var requests = params.inputRequests || params.input_requests;
    if (!requests) return;
    var keys = Object.keys(requests);
    if (!keys.length) return;
    var entry = requests[keys[0]] || {};
    var elicit = entry.params || {};
    question = {
      key: keys[0],
      message: elicit.message || "",
      // Both spellings, because the wire form is camelCase and a host that has
      // already normalized the payload hands over the snake_case one.
      requested_schema: elicit.requestedSchema || elicit.requested_schema || {},
    };
    maybeRender();
  };

  onNotify["ui/notifications/host-context-changed"] = function (params) {
    applyHostContext(Object.assign({}, hostContext, params));
  };

  onNotify["ui/notifications/tool-cancelled"] = function () {
    question = null;
  };

  // The host waits for this response before dropping the frame, which is what
  // makes teardown safe rather than a race against unsaved input.
  onRequest["ui/resource-teardown"] = function () {
    return {};
  };

  onRequest["ping"] = function () {
    return {};
  };

  /**
   * Answer the question by calling the tool again.
   *
   * This is the whole of SEP-2322's client side: the same tool, the same
   * arguments, plus `inputResponses` keyed by the question the server asked. The
   * host proxies it to the server exactly as it would any other `tools/call`
   * from a View.
   */
  function answer(response) {
    if (!question) {
      // Nothing to answer means the host never delivered the tool result, so the
      // person is looking at a form that cannot submit. Say so: a quiet return
      // here leaves the run paused with no sign of why.
      console.error("mcp app has no question to answer: the host sent no tool result");
      return Promise.resolve({});
    }

    var responses = {};
    responses[question.key] = response;
    return request("tools/call", {
      name: toolName,
      arguments: toolArguments,
      inputResponses: responses,
    }).catch(function (err) {
      console.error("mcp app failed to answer", err);
    });
  }

  // The handshake starts as soon as this script runs, which is before the app's
  // own markup has parsed. That is deliberate: the host context (theme, sizing)
  // should be applied before anything paints, and the question can wait for
  // `ready`.
  request("ui/initialize", {
    protocolVersion: PROTOCOL_VERSION,
    clientInfo: { name: "meridian-app", version: "1.0.0" },
    appCapabilities: { availableDisplayModes: ["inline"] },
  })
    .then(function (result) {
      applyHostContext((result || {}).hostContext || {});
      notify("ui/notifications/initialized", {});
    })
    .catch(function (err) {
      console.error("mcp app failed to initialize", err);
    });

  if (typeof ResizeObserver === "function") {
    // Report growth from the content itself rather than on a timer, so a chart
    // that lays out a frame late is not left clipped.
    var observe = function () {
      if (document.body) new ResizeObserver(reportSize).observe(document.body);
    };
    if (document.body) observe();
    else window.addEventListener("DOMContentLoaded", observe);
  }

  window.addEventListener("resize", reportSize);

  return {
    /** Register the renderer. Called once, when the host sends the question. */
    onInit: function (fn) {
      render = fn;
      maybeRender();
    },
    /** Accept: answer the question and let the tool finish. */
    submit: function (content) {
      return answer({ action: "accept", content: content });
    },
    /** Back out of the call entirely. */
    cancel: function () {
      return answer({ action: "cancel" });
    },
    /** Answer this round without content, leaving the tool to decide. */
    decline: function () {
      return answer({ action: "decline" });
    },
    resize: reportSize,
    /** The app's DOM is wired and it can be handed the question. */
    ready: function () {
      wired = true;
      maybeRender();
      reportSize();
    },
    /** Ask the host to open a link, which a sandboxed frame cannot do itself. */
    openLink: function (url) {
      return request("ui/open-link", { url: url });
    },
  };
})();
