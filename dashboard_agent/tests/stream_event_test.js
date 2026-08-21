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
    subagentRoot,
    effectiveNamespace,
    taskBranch,
    parseTaskDispatches,
    isMiddlewareNamespace,
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

  ok("subagentRoot: parallel eval dispatches share a root; distinct calls do not", () => {
    // All parallel task() branches of one eval collapse to the same root, so the
    // fleet card groups them together.
    assert.strictEqual(subagentRoot("tools:eval"), "tools:eval");
    assert.strictEqual(subagentRoot("tools:eval|1"), "tools:eval");
    assert.strictEqual(subagentRoot("tools:eval|2"), "tools:eval");
    // A different eval / task dispatch is a different root (its own group).
    assert.notStrictEqual(subagentRoot("tools:eval|1"), subagentRoot("tools:other"));
    assert.strictEqual(subagentRoot(""), "");
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

  ok("isMiddlewareNamespace: a middleware's own model call is neither main nor sub", () => {
    // The goal grader. Its frames are AI messages with no tool calls, so without
    // this the chat renders the grader's verdict JSON as the assistant's answer.
    assert.strictEqual(
      isMiddlewareNamespace(["RubricMiddleware.after_agent:603ba34e-71df-57f6"]),
      true,
    );
    assert.strictEqual(isMiddlewareNamespace(["SomeMiddleware.before_agent:abc"]), true);
    // The agent's own segments have no dot in the head: main internals and subagents.
    assert.strictEqual(isMiddlewareNamespace([]), false);
    assert.strictEqual(isMiddlewareNamespace(["tools:abc"]), false);
    assert.strictEqual(isMiddlewareNamespace(["model_request:abc"]), false);
    assert.strictEqual(isMiddlewareNamespace(["tools:abc", "model:def"]), false);
  });

  ok("taskBranch: the numeric branch of a bucket key, 0 when there is none", () => {
    assert.strictEqual(taskBranch("tools:eval"), 0);
    assert.strictEqual(taskBranch("tools:eval|1"), 1);
    assert.strictEqual(taskBranch("tools:eval|2"), 2);
    // Named node segments are not branches.
    assert.strictEqual(taskBranch("tools:abc|model:def"), 0);
    assert.strictEqual(taskBranch(""), 0);
  });

  ok("parseTaskDispatches: reads subagentType + description off an eval script", () => {
    const code = [
      "const result = await task({",
      '  description: `Analyse /workspace/data/transactions.csv for account ${acct}`,',
      '  subagentType: "analyst",',
      "  responseSchema: { type: 'object' },",
      "});",
    ].join("\n");
    assert.deepStrictEqual(parseTaskDispatches(code), [
      {
        subagentType: "analyst",
        // `${...}` is code, not information — it reads as an ellipsis.
        description: "Analyse /workspace/data/transactions.csv for account …",
      },
    ]);
  });

  ok("parseTaskDispatches: one entry per dispatch, fields kept with their own call", () => {
    const code = `
      const [a, b] = await Promise.all([
        task({ subagentType: "researcher", description: "Find the policy" }),
        task({ subagentType: "analyst", description: "Compute the rate" }),
      ]);
    `;
    assert.deepStrictEqual(parseTaskDispatches(code), [
      { subagentType: "researcher", description: "Find the policy" },
      { subagentType: "analyst", description: "Compute the rate" },
    ]);
  });

  ok("parseTaskDispatches: snake_case, and a dispatch missing one field", () => {
    assert.deepStrictEqual(parseTaskDispatches('task({ subagent_type: "analyst" })'), [
      { subagentType: "analyst", description: "" },
    ]);
    assert.deepStrictEqual(parseTaskDispatches('task({ description: "Just do it" })'), [
      { subagentType: "", description: "Just do it" },
    ]);
  });

  ok("parseTaskDispatches: a dispatch never borrows the next call's fields", () => {
    const code = 'task({ description: "First" }); task({ subagentType: "analyst" });';
    assert.deepStrictEqual(parseTaskDispatches(code), [
      { subagentType: "", description: "First" },
      { subagentType: "analyst", description: "" },
    ]);
  });

  ok("parseTaskDispatches: a half-streamed call yields nothing, never a partial", () => {
    // The chip's code arrives token by token; an unterminated literal must not
    // render as a truncated instruction, it must simply not match yet.
    assert.deepStrictEqual(parseTaskDispatches("const r = await task({ descrip"), []);
    assert.deepStrictEqual(parseTaskDispatches('task({ description: "half'), []);
    assert.deepStrictEqual(parseTaskDispatches(""), []);
    // Something that merely mentions the word is not a dispatch.
    assert.deepStrictEqual(parseTaskDispatches("// the task({}) helper fans out"), []);
  });

  console.log(`\n${passed} passed`);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
