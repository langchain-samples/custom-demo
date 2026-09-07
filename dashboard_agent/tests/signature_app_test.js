/* Node test for the MCP App's own postMessage contract (mcp_demo_server/signature_app.html).
 *
 * This is the one seam nothing else can reach. The signature pad is HTML the MCP
 * SERVER ships; the SPA renders it inside a sandboxed iframe, so no TypeScript
 * checks it, and vitest cannot execute a srcdoc. If the app's message names or
 * its content keys drift from what `McpElicitationCard` sends and what the
 * `collect_signature` tool's `requested_schema` asks for, nothing fails loudly:
 * the run just stays paused forever.
 *
 * A Node test rather than a vitest one because it needs `node:fs` and `jsdom`
 * directly, and the app's tsconfig has neither in scope (the same reason
 * frontend_test.js and branding_test.js live here).
 *
 * Run: node dashboard_agent/tests/signature_app_test.js
 */
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.join(__dirname, "..", "..");
const APP = path.join(ROOT, "mcp_demo_server", "signature_app.html");
// jsdom is a devDependency of the SPA, which is the only place node_modules lives.
const { JSDOM } = require(path.join(ROOT, "frontend", "node_modules", "jsdom"));

const HTML = fs.readFileSync(APP, "utf8");

let passed = 0;
async function ok(name, fn) {
  await fn();
  passed++;
  console.log("  ok -", name);
}

/**
 * Load the app with its script running.
 *
 * The stubs go in through `beforeParse` because the app's script runs during
 * parsing and captures the canvas context on the way. Posts are collected off
 * the window rather than by replacing `window.parent`: jsdom aliases `parent` to
 * the window itself for a top-level document, and will not let us reassign it.
 */
function mount() {
  const posted = [];
  const dom = new JSDOM(HTML, {
    runScripts: "dangerously",
    pretendToBeVisual: true,
    url: "https://artifact.invalid/",
    beforeParse(window) {
      // jsdom has no 2D canvas. Stub only what the pad touches, so a drawn
      // stroke is observable without a native canvas build.
      window.HTMLCanvasElement.prototype.getContext = () => ({
        setTransform() {}, beginPath() {}, moveTo() {}, lineTo() {}, stroke() {}, clearRect() {},
      });
      window.HTMLCanvasElement.prototype.toDataURL = () => "data:image/png;base64,STUB";
      window.addEventListener("message", (event) => {
        const data = event.data;
        if (data && typeof data.type === "string" && data.type.startsWith("mcp-app:")) {
          posted.push(data);
        }
      });
    },
  });
  return { dom, posted, doc: dom.window.document };
}

/** postMessage is queued, not synchronous: let the queue drain. */
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

/** Draw one mark on the pad, which is what enables Confirm. */
function sign(dom) {
  const canvas = dom.window.document.getElementById("canvas");
  canvas.setPointerCapture = () => {};
  canvas.hasPointerCapture = () => false;
  canvas.getBoundingClientRect = () => ({ left: 0, top: 0, width: 300, height: 150 });
  const down = new dom.window.Event("pointerdown", { bubbles: true });
  Object.assign(down, { clientX: 40, clientY: 40, pointerId: 1 });
  canvas.dispatchEvent(down);
}

/** Fill in the name field the way a person would. */
function name(dom, value) {
  const who = dom.window.document.getElementById("who");
  who.value = value;
  who.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
}

(async () => {
  console.log("signature app (MCP App) contract");

  await ok("announces itself so the host knows to send the request", async () => {
    const { posted } = mount();
    await flush();
    assert.ok(posted.some((m) => m.type === "mcp-app:ready"));
  });

  await ok("asks the host for the height it needs", async () => {
    const { posted } = mount();
    await flush();
    const resize = posted.find((m) => m.type === "mcp-app:resize");
    assert.ok(resize, "no resize message");
    assert.strictEqual(typeof resize.height, "number");
  });

  await ok("shows the server's own question once the host sends it", async () => {
    const { dom, doc } = mount();
    dom.window.postMessage(
      { type: "mcp-app:init", request: { message: "Signature for FL-4501, 9 pallets." } },
      "*",
    );
    await flush();
    assert.strictEqual(doc.getElementById("msg").textContent, "Signature for FL-4501, 9 pallets.");
  });

  await ok("follows the host into dark mode", async () => {
    const { dom, doc } = mount();
    dom.window.postMessage({ type: "mcp-app:init", request: {}, theme: "dark" }, "*");
    await flush();
    assert.strictEqual(doc.documentElement.getAttribute("data-theme"), "dark");
  });

  await ok("stays un-submittable until there is both a signature and a name", async () => {
    const { dom, doc } = mount();
    assert.strictEqual(doc.getElementById("submit").disabled, true);
    sign(dom);
    assert.strictEqual(doc.getElementById("submit").disabled, true, "signed but nobody named");
    name(dom, "Grace Achieng");
    assert.strictEqual(doc.getElementById("submit").disabled, false);
  });

  await ok("posts back exactly the keys the tool's schema asks for", async () => {
    const { dom, doc, posted } = mount();
    sign(dom);
    name(dom, "Grace Achieng");
    doc.getElementById("submit").click();
    await flush();

    const submitted = posted.find((m) => m.type === "mcp-app:submit");
    assert.ok(submitted, "nothing was submitted");
    // These three are `SignatureCapture` on the server. A rename on either side
    // fails validation on resume, and the paused run never continues.
    assert.deepStrictEqual(Object.keys(submitted.content).sort(), [
      "signature",
      "signed_at",
      "signed_by",
    ]);
    assert.ok(submitted.content.signature.startsWith("data:image/png;base64,"));
    assert.strictEqual(submitted.content.signed_by, "Grace Achieng");
    assert.ok(!Number.isNaN(Date.parse(submitted.content.signed_at)));
  });

  await ok("lets the recipient back out, which the host turns into a cancel", async () => {
    const { doc, posted } = mount();
    doc.getElementById("cancel").click();
    await flush();
    assert.ok(posted.some((m) => m.type === "mcp-app:cancel"));
  });

  await ok("clears a stroke, so a bad signature can be redrawn rather than sent", async () => {
    const { dom, doc } = mount();
    sign(dom);
    name(dom, "Grace Achieng");
    assert.strictEqual(doc.getElementById("submit").disabled, false);
    doc.getElementById("clear").click();
    assert.strictEqual(doc.getElementById("submit").disabled, true);
  });

  console.log(`\n${passed} passed`);
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
