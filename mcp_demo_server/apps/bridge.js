/**
 * The bridge every MCP App in this server shares.
 *
 * Each app is HTML served as a `ui://` resource and rendered by the host inside
 * a sandboxed iframe while a tool call is paused on an elicitation. The protocol
 * with the host is identical for all of them, so it lives here and is injected
 * into each app at serve time (see apps.py) rather than copy-pasted four times:
 *
 *   in   { type: "mcp-app:init", request, theme, accent, accentFg }
 *   out  { type: "mcp-app:ready" }              once, on load
 *        { type: "mcp-app:resize", height }     whenever the content grows
 *        { type: "mcp-app:submit", content }    the elicitation answer
 *        { type: "mcp-app:cancel" }             the user backed out
 *
 * `content` must satisfy the elicitation's `requested_schema`, because the host
 * passes it straight back to the server as the accept payload.
 *
 * An app uses it like this:
 *
 *   McpApp.onInit(function (request) { ...render from request... });
 *   McpApp.submit({ approved: true });
 *
 * Everything is inline and dependency-free: the iframe has no origin, so there
 * is nowhere to load a script from.
 */
window.McpApp = (function () {
  "use strict";

  var handler = null;
  var lastHeight = 0;

  function send(msg) {
    // The parent frame is the host; it knows which pause this iframe belongs to.
    parent.postMessage(msg, "*");
  }

  /**
   * Tell the host how tall we are.
   *
   * Called on every render, because an app that grows (a chart appearing, a
   * validation message) would otherwise be clipped by the iframe it is in.
   * Skipped when nothing changed, so a slider drag does not spam the host.
   */
  function resize() {
    var height = document.body.scrollHeight + 4;
    if (Math.abs(height - lastHeight) < 2) return;
    lastHeight = height;
    send({ type: "mcp-app:resize", height: height });
  }

  /** Apply the host's theme, so the app does not read as a foreign page. */
  function theme(data) {
    if (data.theme === "dark") document.documentElement.setAttribute("data-theme", "dark");
    var root = document.documentElement.style;
    if (data.accent) root.setProperty("--accent", data.accent);
    if (data.accentFg) root.setProperty("--accent-fg", data.accentFg);
  }

  window.addEventListener("message", function (event) {
    var data = event.data;
    if (!data || data.type !== "mcp-app:init") return;
    theme(data);
    if (handler) {
      try {
        handler(data.request || {}, data);
      } catch (err) {
        // A broken app must still let the person out of the pause, or the run
        // is stuck with no way to answer it.
        console.error("mcp app failed to render", err);
      }
    }
    resize();
  });

  window.addEventListener("resize", resize);

  return {
    /** Register the renderer. Called once, when the host sends the request. */
    onInit: function (fn) {
      handler = fn;
    },
    /** Answer the elicitation and let the tool finish. */
    submit: function (content) {
      send({ type: "mcp-app:submit", content: content });
    },
    /** Abandon the tool call. */
    cancel: function () {
      send({ type: "mcp-app:cancel" });
    },
    resize: resize,
    /** Announce readiness. Every app calls this last, after wiring its DOM. */
    ready: function () {
      send({ type: "mcp-app:ready" });
      resize();
    },
  };
})();
