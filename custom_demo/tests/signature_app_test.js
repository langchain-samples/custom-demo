/* Node test for the MCP App's own wire contract (mcp_demo_server/apps/src/signature.tsx).
 *
 * This is the one seam nothing else can reach. The signature pad is a React
 * component the MCP SERVER ships, compiled into apps/app.js and inlined into
 * the document the SPA renders inside a sandboxed iframe. No type check reaches
 * the wire, and vitest cannot execute a srcdoc. If the app's messages or its
 * content keys drift from what a host sends and what the `submit_signature`
 * tool accepts, nothing fails loudly: the signature is simply lost after
 * somebody drew it.
 *
 * The harness below is a minimal MCP Apps host, because the contract under test
 * is SEP-1865: the app opens with `ui/initialize`, is handed the finished call
 * over `ui/notifications/tool-result`, and submits by calling
 * `submit_signature`, which the server marks `visibility: ["app"]`. Pinning
 * those names here is what keeps the app renderable by any host, not only ours.
 *
 * The document under test is the one the server really serves, obtained by
 * calling `render_app` rather than by composing the pieces here. Composition
 * order is load-bearing: the bundle's last statement looks `#root` up by id, so
 * a `<script>` that moved into the head would throw before any app rendered,
 * and a hand-built copy of the document would never notice.
 *
 * A Node test rather than a vitest one because it needs `node:fs` and `jsdom`
 * directly, and the app's tsconfig has neither in scope (the same reason
 * frontend_test.js and branding_test.js live here).
 *
 * Run: node custom_demo/tests/signature_app_test.js
 */
const assert = require("node:assert");
const { execFileSync } = require("node:child_process");
const path = require("node:path");

const ROOT = path.join(__dirname, "..", "..");
// jsdom is a devDependency of the SPA, which is the only place node_modules lives.
const { JSDOM } = require(path.join(ROOT, "frontend", "node_modules", "jsdom"));

/** The signature app exactly as `apps.py` serves it. */
const HTML = execFileSync(
  "python3",
  [
    "-c",
    // apps.py is loaded by path rather than imported, so this needs no
    // virtualenv: the module itself is stdlib-only, while importing the
    // package would pull in fastmcp through its `__init__`.
    "import importlib.util, sys\n" +
      "spec = importlib.util.spec_from_file_location('apps', 'mcp_demo_server/apps.py')\n" +
      "mod = importlib.util.module_from_spec(spec)\n" +
      "spec.loader.exec_module(mod)\n" +
      "sys.stdout.write(mod.render_app('signature', title='Signature'))\n",
  ],
  // The document is most of a megabyte, nearly all of it the React bundle.
  { cwd: ROOT, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 },
);

let passed = 0;

/**
 * Every window a test opened, so it can be shut when the test ends.
 *
 * Closing matters for the clock, not for tidiness: a `tools/call` the test
 * never answers leaves the SDK's request timeout pending, and node will not
 * exit until every one of those has expired. Fifteen abandoned windows turned a
 * suite that finishes its assertions in a second into a minute of waiting.
 */
const live = [];

async function ok(name, fn) {
  try {
    await fn();
  } finally {
    while (live.length) live.pop().close();
  }

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
        save() {}, restore() {},
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
  live.push(dom.window);
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
  reply(dom.window, dom.window.parent, {
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
  });
}

/** What the pad submitted, as the host receives it. */
function answered(posted) {
  const call = posted.find((m) => m.method === "tools/call" && m.params.name === SUBMIT);
  return call ? call.params.arguments.capture : undefined;
}

/**
 * Let the queues drain.
 *
 * Two of them, which is why this is more than one turn: postMessage is
 * delivered as a task, and React renders on the scheduler's own task rather
 * than inside the event that queued the state update. Several turns of the
 * event loop covers a handshake reply landing, a render, and an effect that
 * sends the next message.
 */
const flush = async () => {
  for (let i = 0; i < 6; i++) await new Promise((resolve) => setTimeout(resolve, 0));
};

/**
 * Let the queues drain AND the animation frame the SDK measures on arrive.
 *
 * `flush` is macrotask turns, which all run inside the first frame; auto-resize
 * measures inside a `requestAnimationFrame`, so anything asserting on sizing
 * has to wait for a real frame rather than a queue.
 */
const settle = async () => {
  await flush();
  await new Promise((resolve) => setTimeout(resolve, 50));
  await flush();
};

/**
 * A mounted pad with the handshake finished and the tool result delivered.
 *
 * Every test that touches a control has to get past both. The mount in
 * src/index.tsx renders a status line and withholds the component until the
 * result arrives, so there is no moment at which the pad exists with nothing
 * to label itself: before that point there is no `#who` and no `#submit` to
 * find.
 */
async function open(options = {}) {
  const harness = mount(options.dataUri, options.theme);
  await flush();
  deliver(harness.dom, options.document);
  await flush();
  return harness;
}

/** Make the canvas measurable, since jsdom lays nothing out. */
function pad(dom, width) {
  const canvas = dom.window.document.getElementById("canvas");
  canvas.setPointerCapture = () => {};
  canvas.hasPointerCapture = () => false;
  canvas.getBoundingClientRect = () => ({ left: 0, top: 0, width, height: 150 });
  return canvas;
}

/** Dispatch one pointer event at a point on the pad. */
async function stroke(dom, canvas, type, x, y) {
  const event = new dom.window.Event(type, { bubbles: true });
  Object.assign(event, { clientX: x, clientY: y, pointerId: 1 });
  canvas.dispatchEvent(event);
  await flush();
}

/** Draw one mark on the pad, which is what enables Confirm. */
async function sign(dom) {
  await stroke(dom, pad(dom, 300), "pointerdown", 40, 40);
}

/**
 * Draw a stroke across most of the pad, the way a real signature runs.
 *
 * The size ladder never upscales, so a box narrower than the target width is
 * already at its smallest and the retries are a no-op - which is right, and is
 * why watching the ladder work needs a wide mark rather than the single dot.
 */
async function signWide(dom) {
  const canvas = pad(dom, 600);
  await stroke(dom, canvas, "pointerdown", 20, 30);
  await stroke(dom, canvas, "pointermove", 580, 120);
}

/** Fill in the name field the way a person would. */
async function name(dom, value) {
  const who = dom.window.document.getElementById("who");
  // Through the prototype's own setter, because React installs one of its own
  // on the node to track the value it last rendered. Assigning `who.value`
  // updates that tracker as a side effect, and React then compares the two,
  // sees no change, and never calls the input's onChange.
  const native = Object.getOwnPropertyDescriptor(
    dom.window.HTMLInputElement.prototype,
    "value",
  ).set;
  native.call(who, value);
  who.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  await flush();
}

/** Press Confirm and let the submission go out. */
async function confirm(doc) {
  doc.getElementById("submit").click();
  await flush();
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

  await ok("reports its height from one place, not from each app", async () => {
    const { dom, posted } = mount();
    await flush();
    deliver(dom);
    await settle();
    // `useApp` in src/index.tsx turns on the SDK's auto-resize, which measures
    // the document and reports it. That one notification is the whole sizing
    // story for all four apps, and a component that measured itself as well
    // would show up here as a second: two reporters fighting over the frame
    // height is how a pane ends up flickering or clipped.
    const sizes = posted.filter((m) => m.method === "ui/notifications/size-changed");
    assert.strictEqual(sizes.length, 1, `expected exactly one reporter, got ${sizes.length}`);
    assert.strictEqual(sizes[0].params.width, dom.window.innerWidth);
  });

  await ok("says which app the bundle is to draw", async () => {
    // One bundle carries all four apps, so the document has to name the one it
    // is. A missing or misspelled name renders a pane saying there is no such
    // app, which on stage is a demo with nothing in it.
    const { doc } = mount();
    assert.strictEqual(doc.getElementById("root").dataset.app, "signature");
  });

  await ok("waits for the tool result rather than drawing an empty pad", async () => {
    // The pad labels itself from the result, and a control that appears before
    // its data would be a pad captioned with a placeholder.
    const { doc } = mount();
    await flush();
    assert.strictEqual(doc.getElementById("submit"), null);
    assert.match(doc.querySelector(".msg").textContent, /tool result/i);
  });

  await ok("titles itself from the tool's own result", async () => {
    const { doc } = await open({ document: "IPS amendment" });
    // The app titles itself from the result, naming the document and the household.
    const shown = doc.getElementById("msg").textContent;
    assert.ok(shown.includes("IPS amendment"), shown);
    assert.ok(shown.includes("Whitfield Family Trust"), shown);
  });

  await ok("follows the host into dark mode", async () => {
    const { doc } = await open({ theme: "dark" });
    assert.strictEqual(doc.documentElement.getAttribute("data-theme"), "dark");
  });

  await ok("stays un-submittable until there is both a signature and a name", async () => {
    const { dom, doc } = await open();
    assert.strictEqual(doc.getElementById("submit").disabled, true);
    await sign(dom);
    assert.strictEqual(doc.getElementById("submit").disabled, true, "signed but nobody named");
    await name(dom, "Grace Achieng");
    assert.strictEqual(doc.getElementById("submit").disabled, false);
  });

  await ok("posts back exactly the keys the tool's schema asks for", async () => {
    const { dom, doc, posted } = await open();
    await sign(dom);
    await name(dom, "Grace Achieng");
    await confirm(doc);

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
    const { dom, doc, draws } = await open();
    await sign(dom);
    await name(dom, "Grace Achieng");
    await confirm(doc);

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
    const { dom, doc, draws, posted } = await open({
      dataUri: "data:image/png;base64," + "A".repeat(12000),
    });
    await signWide(dom);
    await name(dom, "Grace Achieng");
    await confirm(doc);

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
    const { dom, doc, draws } = await open();
    await signWide(dom);
    await name(dom, "Grace Achieng");
    await confirm(doc);
    assert.strictEqual(draws.length, 1, "a signature under budget should not be re-encoded");
  });

  await ok("offers no way to back out, because nothing is waiting on it", async () => {
    const { doc } = await open();
    // The tool call finished before this app was rendered. A Cancel button would
    // be a promise the app cannot keep, so Clear is the only way back.
    assert.strictEqual(doc.getElementById("cancel"), null);
    assert.ok(doc.getElementById("clear"), "the pad still needs a way to redraw");
  });

  await ok("clears a stroke, so a bad signature can be redrawn rather than sent", async () => {
    const { dom, doc } = await open();
    await sign(dom);
    await name(dom, "Grace Achieng");
    assert.strictEqual(doc.getElementById("submit").disabled, false);
    doc.getElementById("clear").click();
    await flush();
    assert.strictEqual(doc.getElementById("submit").disabled, true);
  });

  await ok("re-arms Confirm when the server refuses, so a refusal is not final", async () => {
    // The pad holds the only copy of the drawn signature. A submission that
    // fails and leaves the button dead loses it, with the stroke still on
    // screen and no way to send it.
    const { dom, doc, posted } = await open();
    await sign(dom);
    await name(dom, "Grace Achieng");
    await confirm(doc);

    const call = posted.find((m) => m.method === "tools/call");
    reply(dom.window, dom.window.parent, {
      jsonrpc: "2.0",
      id: call.id,
      result: { content: [], structuredContent: { error: "Signature rejected." } },
    });
    await flush();
    assert.strictEqual(doc.getElementById("msg").textContent, "Signature rejected.");
    assert.strictEqual(doc.getElementById("submit").disabled, false, "Confirm stayed dead");
  });

  console.log(`\n${passed} passed`);
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
