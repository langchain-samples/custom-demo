/* Our hand-written MCP App view, checked against the SDK's own schemas.
 *
 * The HOST is the official SDK's now (frontend/src/lib/mcpAppHost.ts wraps
 * `AppBridge`), but the VIEW is still ours: mcp_demo_server/apps/bridge.js is
 * vanilla JS inlined into an origin-less iframe, where bundling React is not
 * free. That leaves one gap nothing else covers.
 *
 * When BOTH halves are hand-written they can agree with each other and disagree
 * with the spec, silently, forever. That is not hypothetical: bridge.js sent
 * `clientInfo` where `ui/initialize` requires `appInfo`, our old hand-written
 * host never validated it, and the pair worked perfectly right up until a
 * conformant host refused the handshake.
 *
 * So this parses what bridge.js actually sends using the schemas the SDK
 * publishes. It reads the real file rather than a copy, because a copy would
 * drift in exactly the way this exists to catch.
 *
 * A Node test rather than a vitest one for the same reason as its neighbours:
 * it needs `node:fs`, which the app's tsconfig does not have in scope.
 *
 * Run: node custom_demo/tests/mcp_app_conformance_test.js
 */
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.join(__dirname, "..", "..");
const BRIDGE = fs.readFileSync(
  path.join(ROOT, "mcp_demo_server", "apps", "bridge.js"),
  "utf8",
);
const SDK = path.join(
  ROOT, "frontend", "node_modules", "@modelcontextprotocol", "ext-apps",
  "dist", "src", "app-bridge.js",
);

let passed = 0;
async function ok(name, fn) {
  await fn();
  passed++;
  console.log("  ok -", name);
}

/** The literal object bridge.js passes to `request("ui/initialize", ...)`. */
function initializeParams(version) {
  const body = /request\("ui\/initialize",\s*\{([\s\S]*?)\n {2}\}\)/.exec(BRIDGE);
  assert.ok(body, "could not find the ui/initialize call in bridge.js");
  const src = body[1]
    .replace(/\/\/[^\n]*/g, "")
    .replace(/PROTOCOL_VERSION/g, JSON.stringify(version));
  return Function(`"use strict"; return ({${src}});`)();
}

(async () => {
  console.log("MCP App conformance (our view vs the SDK's schemas)");
  const sdk = await import(SDK);
  const version = sdk.SUPPORTED_PROTOCOL_VERSIONS[0];

  await ok("opens a handshake the SDK accepts", async () => {
    const parsed = sdk.McpUiInitializeRequestSchema.safeParse({
      method: "ui/initialize",
      params: initializeParams(version),
    });
    // `appInfo`, not `clientInfo`. Getting this wrong broke nothing locally and
    // would have broken every app against a conformant host.
    assert.ok(parsed.success, JSON.stringify(parsed.error && parsed.error.issues));
  });

  await ok("declares a protocol version the SDK supports", async () => {
    const declared = /var PROTOCOL_VERSION = "([^"]+)"/.exec(BRIDGE)[1];
    assert.ok(
      sdk.SUPPORTED_PROTOCOL_VERSIONS.includes(declared),
      `${declared} is not in ${JSON.stringify(sdk.SUPPORTED_PROTOCOL_VERSIONS)}`,
    );
  });

  await ok("declares the display modes it can handle, which is a MUST", async () => {
    const caps = initializeParams(version).appCapabilities || {};
    // A host may not move a view into a mode absent from this list, so an empty
    // one silently forfeits fullscreen.
    assert.ok((caps.availableDisplayModes || []).length > 0);
  });

  await ok("sends size updates under the name the SDK listens for", async () => {
    // A renamed notification is invisible: the frame simply never resizes.
    assert.ok(BRIDGE.includes(sdk.SIZE_CHANGED_METHOD), sdk.SIZE_CHANGED_METHOD);
  });

  await ok("reads the tool result under the name the SDK sends", async () => {
    assert.ok(BRIDGE.includes(sdk.TOOL_RESULT_METHOD), sdk.TOOL_RESULT_METHOD);
    assert.ok(BRIDGE.includes(sdk.TOOL_INPUT_METHOD), sdk.TOOL_INPUT_METHOD);
  });

  console.log(`\n${passed} passed`);
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
