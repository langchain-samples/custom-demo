/* Node test for the subagent-namespace stream routing helpers (pure functions).
 *
 * Imports the REAL module the app ships (frontend/src/lib/streamEvent.ts) via
 * Node's native type stripping, like chart_test.js. Guards the "peer into
 * subagents" leak-prevention contract: how SSE event names + checkpoint
 * namespaces map to main-graph vs subagent buckets.
 *
 * Run: node dashboard_agent/tests/stream_event_test.js
 */
const assert = require("node:assert");
const path = require("node:path");

const MOD = path.join(__dirname, "..", "..", "frontend", "src", "lib", "streamEvent.ts");

let passed = 0;
function ok(name, fn) {
  fn();
  passed++;
  console.log("  ok -", name);
}

(async () => {
  const {
    splitStreamEvent,
    parseCheckpointNs,
    isSubagentNamespace,
    subagentIdentity,
    effectiveNamespace,
  } = await import(MOD);

  ok("splitStreamEvent: plain event is the main graph (empty namespace)", () => {
    assert.deepStrictEqual(splitStreamEvent("messages/partial"), {
      event: "messages/partial",
      namespace: [],
    });
  });

  ok("splitStreamEvent: one `|` suffix is a single-segment subagent namespace", () => {
    assert.deepStrictEqual(splitStreamEvent("messages/partial|tools:abc"), {
      event: "messages/partial",
      namespace: ["tools:abc"],
    });
  });

  ok("splitStreamEvent: nested segments split into the full path", () => {
    assert.deepStrictEqual(splitStreamEvent("messages/complete|tools:abc|model_request:def"), {
      event: "messages/complete",
      namespace: ["tools:abc", "model_request:def"],
    });
  });

  ok("splitStreamEvent: trailing/empty segments are dropped (no phantom ns)", () => {
    assert.deepStrictEqual(splitStreamEvent("messages/partial|"), {
      event: "messages/partial",
      namespace: [],
    });
    assert.deepStrictEqual(splitStreamEvent(""), { event: "", namespace: [] });
  });

  ok("parseCheckpointNs: splits a checkpoint_ns string; empty/undefined -> []", () => {
    assert.deepStrictEqual(parseCheckpointNs("tools:abc|model_request:def"), [
      "tools:abc",
      "model_request:def",
    ]);
    assert.deepStrictEqual(parseCheckpointNs(""), []);
    assert.deepStrictEqual(parseCheckpointNs(undefined), []);
    assert.deepStrictEqual(parseCheckpointNs(null), []);
  });

  ok("isSubagentNamespace: empty is main; tools:* is a subagent", () => {
    assert.strictEqual(isSubagentNamespace([]), false);
    assert.strictEqual(isSubagentNamespace(["tools:abc"]), true);
    assert.strictEqual(isSubagentNamespace(["tools:abc", "model_request:def"]), true);
    // A non-tools namespace is not treated as an observable subagent.
    assert.strictEqual(isSubagentNamespace(["model_request:def"]), false);
  });

  ok("subagentIdentity: keys on the first segment; reads 'tools' as 'Subagent'", () => {
    assert.deepStrictEqual(subagentIdentity(["tools:abc"]), {
      key: "tools:abc",
      label: "Subagent",
    });
    // Nested NAMED nodes roll up under the SAME top-level subagent instance.
    assert.deepStrictEqual(subagentIdentity(["tools:abc", "model_request:def"]), {
      key: "tools:abc",
      label: "Subagent",
    });
    // Two distinct dispatches get distinct keys.
    assert.notStrictEqual(
      subagentIdentity(["tools:abc"]).key,
      subagentIdentity(["tools:xyz"]).key,
    );
    // Parallel task()-from-code dispatches share the eval tool-call id and differ
    // only by a trailing NUMERIC branch — each is its own subagent card.
    assert.strictEqual(subagentIdentity(["tools:eval", "1"]).key, "tools:eval|1");
    assert.strictEqual(subagentIdentity(["tools:eval", "2"]).key, "tools:eval|2");
    assert.notStrictEqual(
      subagentIdentity(["tools:eval"]).key, // bare = first parallel branch
      subagentIdentity(["tools:eval", "1"]).key,
    );
    // A numeric branch that then descends into a named node still rolls the named
    // node up, keeping the branch: tools:eval|1|model:x -> tools:eval|1.
    assert.strictEqual(
      subagentIdentity(["tools:eval", "1", "model:x"]).key,
      "tools:eval|1",
    );
  });

  ok("effectiveNamespace: event ns wins; else falls back to nsById; else main", () => {
    const nsById = { m1: ["tools:abc"] };
    // Event-name namespace present -> use it directly.
    assert.deepStrictEqual(effectiveNamespace(["tools:xyz"], "m1", nsById), ["tools:xyz"]);
    // Empty event ns + known message id -> fall back to the recorded checkpoint ns.
    assert.deepStrictEqual(effectiveNamespace([], "m1", nsById), ["tools:abc"]);
    // Empty event ns + unknown id -> main graph.
    assert.deepStrictEqual(effectiveNamespace([], "m2", nsById), []);
    assert.deepStrictEqual(effectiveNamespace([], undefined, nsById), []);
  });

  console.log(`\n${passed} passed`);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
