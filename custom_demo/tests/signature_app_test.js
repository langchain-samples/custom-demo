/* Node test for the MCP App's own wire contract (mcp_demo_server/apps/signature.html).
 *
 * This is the one seam nothing else can reach. The signature pad is HTML the MCP
 * SERVER ships; the SPA renders it inside a sandboxed iframe, so no TypeScript
 * checks it, and vitest cannot execute a srcdoc. If the app's messages or its
 * content keys drift from what a host sends and what the `sign_document` tool's
 * `requested_schema` asks for, nothing fails loudly: the run just stays paused
 * forever.
 *
 * The harness below is a minimal MCP Apps host, because the contract under test
 * is SEP-1865: the app opens with `ui/initialize`, is handed the finished call
 * over `ui/notifications/tool-input` and `ui/notifications/tool-result`, and
 * submits by calling `submit_signature`, which the server marks
 * `visibility: ["app"]`. Pinning those names here is what keeps the app
 * renderable by any host, not only ours.
 *
 * A Node test rather than a vitest one because it needs `node:fs` and `jsdom`
 * directly, and the app's tsconfig has neither in scope (the same reason
 * frontend_test.js and branding_test.js live here).
 *
 * Run: node custom_demo/tests/signature_app_test.js
 */
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.join(__dirname, "..", "..");
const APPS = path.join(ROOT, "mcp_demo_server", "apps");
// jsdom is a devDependency of the SPA, which is the only place node_modules lives.
const { JSDOM } = require(path.join(ROOT, "frontend", "node_modules", "jsdom"));

/** Compose the app the way apps.py serves it: shared shell + bridge, then the app. */
const HTML =
  "<!doctype html><html><head><style>" +
  fs.readFileSync(path.join(APPS, "shell.css"), "utf8") +
  "</style><script>" +
  fs.readFileSync(path.join(APPS, "bridge.js"), "utf8") +
  "</script></head><body>" +
  fs.readFileSync(path.join(APPS, "signature.html"), "utf8") +
  "</body></html>";

let passed = 0;
async function ok(name, fn) {
  await fn();
  passed++;
  console.log("  ok -", name);
}

/** The app-only tool the pad submits to (see server.py). */
const SUBMIT = "submit_signature";

/** What the host answers `ui/initialize` with. */
function initializeResult(theme) {
  return {
    protocolVersion: "2026-01-26",
    hostInfo: { name: "test-host", version: "1.0.0" },
    hostCapabilities: { serverTools: {} },
    hostContext: {
      // `inputSchema` is REQUIRED on `Tool`, and the SDK validates this result.
      // Leaving it out makes the app refuse the handshake, which is exactly the
      // bug a real third-party app caught in our host.
      toolInfo: {
        tool: {
          name: "meridian_sign_document",
          inputSchema: { type: "object", properties: { document: { type: "string" } } },
        },
      },
      theme: theme || "light",
      displayMode: "inline",
      containerDimensions: { maxHeight: 640 },
    },
  };
}

/**
 * Load the app with its script running, behind a stub host.
 *
 * The stubs go in through `beforeParse` because the app's script runs during
 * parsing and opens the handshake on the way. `window.parent` is replaced so the
 * app's messages land on the host stub rather than back on its own listener:
 * jsdom aliases `parent` to the window itself for a top-level document, which
 * would otherwise let the app answer its own `ui/initialize`.
 */
function mount(dataUri, theme) {
  const posted = [];
  const draws = [];
  // What the stubbed canvas encodes to. Long enough to blow the budget when a
  // test wants to watch the export shrink.
  const uri = () => dataUri || "data:image/png;base64,STUB";
  const dom = new JSDOM(HTML, {
    runScripts: "dangerously",
    pretendToBeVisual: true,
    url: "https://artifact.invalid/",
    beforeParse(window) {
      // The SDK logs every frame at debug level, which buries the test output.
      window.console.debug = () => {};
      // jsdom has no ResizeObserver, and the SDK's `autoResize` uses one to
      // report height. Stub it: the app must still load without one.
      window.ResizeObserver = class {
        constructor(cb) {
          this.cb = cb;
        }
        // Fire once on observe, standing in for the initial measurement a real
        // one delivers. Without it the SDK's autoResize never reports a height
        // and the app looks like it forgot to.
        observe(target) {
          setTimeout(() => this.cb([{ target, contentRect: { width: 320, height: 240 } }], this), 0);
        }
        unobserve() {}
        disconnect() {}
      };
      // jsdom has no 2D canvas. Stub only what the pad touches, so a drawn
      // stroke is observable without a native canvas build.
      window.HTMLCanvasElement.prototype.getContext = () => ({
        setTransform() {}, beginPath() {}, moveTo() {}, lineTo() {}, stroke() {}, clearRect() {},
        // Records the crop the export asks for, which is the thing worth checking:
        // a full-pad export is what made the PNG an order of magnitude too big.
        drawImage(...args) {
          draws.push(args.slice(1));
        },
        // `flatten` reads pixels back to collapse the antialiasing; jsdom has
        // none, so hand it a buffer of the right shape.
        getImageData: (x, y, w, h) => ({ data: new Uint8ClampedArray(w * h * 4), width: w, height: h }),
        putImageData() {},
      });
      window.HTMLCanvasElement.prototype.toDataURL = () => uri();
      // The SDK's transport only accepts messages whose `event.source` is the
      // parent it posts to. `window.postMessage` cannot set that, so the host
      // stub dispatches a MessageEvent itself, which is what a browser does
      // when a parent posts into a child frame.
      const host = {
        postMessage(msg) {
          posted.push(msg);
          // The only thing a host must do for the app to get going: answer the
          // handshake. Everything after it is driven by the tests.
          if (msg.method === "ui/initialize") {
            reply(window, host, { jsonrpc: "2.0", id: msg.id, result: initializeResult(theme) });
          }
        },
      };
      Object.defineProperty(window, "parent", { configurable: true, value: host });
    },
  });
  return { dom, posted, draws, doc: dom.window.document, host: () => dom.window.parent };
}

/** Deliver a message to the view the way a parent window does. */
function reply(window, source, data) {
  window.dispatchEvent(new window.MessageEvent("message", { data, source }));
}

/** Find the first message the app sent with this JSON-RPC method. */
const sent = (posted, method) => posted.find((m) => m.method === method);

/**
 * Hand the app the finished call, the way a host does after the handshake.
 *
 * `sign_document` returns the document's label and reference; the pad draws
 * itself from that. There is no pause and no question: this is an ordinary
 * result, delivered on the ordinary notification.
 */
function deliver(dom, document_) {
  const src = dom.window.parent;
  reply(dom.window, src, 
    {
      jsonrpc: "2.0",
      method: "ui/notifications/tool-input",
      params: { arguments: { account_id: "MW-10241", document: document_ || "IPS amendment" } },
    },
  );
  reply(dom.window, src, 
    {
      jsonrpc: "2.0",
      method: "ui/notifications/tool-result",
      params: {
        structuredContent: {
          account_id: "MW-10241",
          household: "Whitfield Family Trust",
          document: document_ || "IPS amendment",
          reference: "MW-DOC-04417",
        },
      },
    },
  );
}

/** What the pad submitted, as the host receives it. */
function answered(posted) {
  const call = posted.find((m) => m.method === "tools/call" && m.params.name === SUBMIT);
  return call ? call.params.arguments.capture : undefined;
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

/**
 * Draw a stroke across most of the pad, the way a real signature runs.
 *
 * The size ladder never upscales, so a box narrower than the target width is
 * already at its smallest and the retries are a no-op - which is right, and is
 * why watching the ladder work needs a wide mark rather than the single dot.
 */
function signWide(dom) {
  const canvas = dom.window.document.getElementById("canvas");
  canvas.setPointerCapture = () => {};
  canvas.hasPointerCapture = () => false;
  canvas.getBoundingClientRect = () => ({ left: 0, top: 0, width: 600, height: 150 });
  const down = new dom.window.Event("pointerdown", { bubbles: true });
  Object.assign(down, { clientX: 20, clientY: 30, pointerId: 1 });
  canvas.dispatchEvent(down);
  const move = new dom.window.Event("pointermove", { bubbles: true });
  Object.assign(move, { clientX: 580, clientY: 120, pointerId: 1 });
  canvas.dispatchEvent(move);
}

/** Fill in the name field the way a person would. */
function name(dom, value) {
  const who = dom.window.document.getElementById("who");
  who.value = value;
  who.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
}

(async () => {
  console.log("signature app (MCP App) contract");

  await ok("opens the SEP-1865 handshake without being prompted", async () => {
    const { posted } = mount();
    await flush();
    const hello = sent(posted, "ui/initialize");
    assert.ok(hello, "the app never sent ui/initialize");
    assert.strictEqual(hello.jsonrpc, "2.0");
    assert.strictEqual(hello.params.protocolVersion, "2026-01-26");
    // Declaring display modes is a MUST: a host may not move a View into a mode
    // it never claimed.
    // Spread first: these objects come from the jsdom realm, so their prototypes
    // are not the ones deepStrictEqual compares against.
    assert.deepStrictEqual([...hello.params.appCapabilities.availableDisplayModes], ["inline"]);
  });

  await ok("confirms the handshake so the host may start sending", async () => {
    const { posted } = mount();
    await flush();
    const done = sent(posted, "ui/notifications/initialized");
    assert.ok(done, "the app never confirmed initialization");
    assert.strictEqual(done.id, undefined, "a notification must not carry an id");
  });

  await ok("leaves sizing to the SDK rather than hand-rolling it", async () => {
    const { posted } = mount();
    await flush();
    // The SDK reports height through a ResizeObserver (`autoResize`, on by
    // default), so the app sends no size notification of its own. Whether that
    // observer fires is not testable here: jsdom has no layout, every element
    // measures 0, and the SDK correctly sends nothing when nothing changed.
    // What IS ours is not duplicating it.
    const ours = posted.filter((m) => m.method === "ui/notifications/size-changed");
    assert.strictEqual(ours.length, 0, "the app should not send its own size notifications");
  });

  await ok("titles itself from the tool's own result", async () => {
    const { dom, doc } = mount();
    await flush();
    deliver(dom, "IPS amendment");
    await flush();
    // The app titles itself from the result, naming the document and the household.
    const shown = doc.getElementById("msg").textContent;
    assert.ok(shown.includes("IPS amendment"), shown);
    assert.ok(shown.includes("Whitfield Family Trust"), shown);
  });

  await ok("follows the host into dark mode", async () => {
    const { doc } = mount(undefined, "dark");
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
    await flush();
    deliver(dom);
    await flush();
    sign(dom);
    name(dom, "Grace Achieng");
    doc.getElementById("submit").click();
    await flush();

    const call = posted.find((m) => m.method === "tools/call");
    assert.ok(call, "nothing was submitted");
    // An ordinary tool call to the app-only tool, carrying what the person did.
    assert.strictEqual(call.params.name, SUBMIT);
    assert.strictEqual(call.params.arguments.account_id, "MW-10241");
    assert.strictEqual(call.params.arguments.document, "IPS amendment");

    const submitted = answered(posted);
    // These three are `SignatureCapture` on the server. A rename on either side
    // fails validation, and the signature is lost after it was drawn.
    assert.deepStrictEqual(Object.keys(submitted).sort(), [
      "signature",
      "signed_at",
      "signed_by",
    ]);
    assert.ok(submitted.signature.startsWith("data:image/png;base64,"));
    assert.strictEqual(submitted.signed_by, "Grace Achieng");
    assert.ok(!Number.isNaN(Date.parse(submitted.signed_at)));
  });

  await ok("exports only the ink, not the whole pad", async () => {
    const { dom, doc, draws } = mount();
    await flush();
    deliver(dom);
    await flush();
    sign(dom);
    name(dom, "Grace Achieng");
    doc.getElementById("submit").click();
    await flush();

    const [sx, sy, sw, sh] = draws[0];
    // The stroke is a dot at (40, 40) with an 8px margin, so the source rect is
    // a small box near the origin - NOT the 300x150 pad. Device pixels, so the
    // ratio is folded in; jsdom reports 1.
    assert.ok(sw < 40 && sh < 40, `expected a cropped source rect, got ${sw}x${sh}`);
    assert.ok(sx >= 0 && sy >= 0 && sx < 40 && sy < 40, `crop is not around the ink: ${sx},${sy}`);
  });

  await ok("shrinks the export until it fits the budget", async () => {
    // Every width encodes to 12KB here, over the 10KB budget, so the export
    // should work down the ladder instead of sending the first thing it made.
    const { dom, doc, draws, posted } = mount("data:image/png;base64," + "A".repeat(12000));
    await flush();
    deliver(dom);
    await flush();
    signWide(dom);
    name(dom, "Grace Achieng");
    doc.getElementById("submit").click();
    await flush();

    assert.ok(draws.length > 1, `expected retries at smaller widths, got ${draws.length}`);
    // Destination width is the 6th drawImage arg. Non-increasing rather than
    // strictly falling: the ladder never UPSCALES, so every rung wider than the
    // ink box renders at the box's own size, and only the rungs below it shrink.
    const widths = draws.map((d) => d[6]);
    for (let i = 1; i < widths.length; i++) {
      assert.ok(widths[i] <= widths[i - 1], `width grew: ${widths.join(", ")}`);
    }
    assert.ok(widths[widths.length - 1] < widths[0], `never shrank: ${widths.join(", ")}`);
    // Over budget at every size, it still sends one rather than nothing: a
    // rough signature beats a document with no signature on it.
    assert.ok(answered(posted).signature);
  });

  await ok("sends the first encoding when it already fits", async () => {
    const { dom, doc, draws } = mount();
    await flush();
    deliver(dom);
    await flush();
    signWide(dom);
    name(dom, "Grace Achieng");
    doc.getElementById("submit").click();
    await flush();
    assert.strictEqual(draws.length, 1, "a signature under budget should not be re-encoded");
  });

  await ok("offers no way to back out, because nothing is waiting on it", async () => {
    const { doc } = mount();
    await flush();
    // The tool call finished before this app was rendered. A Cancel button would
    // be a promise the app cannot keep, so Clear is the only way back.
    assert.strictEqual(doc.getElementById("cancel"), null);
    assert.ok(doc.getElementById("clear"), "the pad still needs a way to redraw");
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
