/* Node test for LangSmith trace-project naming (frontend/src/lib/trace.ts).
 *
 * The convention is duplicated in dashboard_agent/assistant_setup.py, so these
 * assertions are what keeps the two from drifting.
 *
 * Run: node dashboard_agent/tests/trace_test.js
 */
const assert = require("node:assert");
const path = require("node:path");

const MOD = path.join(__dirname, "..", "..", "frontend", "src", "lib", "trace.ts");

let passed = 0;
function ok(name, fn) {
  fn();
  passed++;
  console.log("  ok -", name);
}

(async () => {
  const { traceProject } = await import(MOD);

  ok("project is the customer name", () => {
    assert.strictEqual(traceProject({ metadata: { customer: "Acme Health" } }), "Acme Health");
    assert.strictEqual(traceProject({ metadata: { customer: "  Walmart  " } }), "Walmart");
  });

  ok("falls back through customer -> name -> id", () => {
    assert.strictEqual(traceProject({ name: "Vizient" }), "Vizient");
    assert.strictEqual(traceProject(null, "Globex"), "Globex");
    assert.strictEqual(traceProject(null, ""), "customer");
  });

  // A DE can point a demo at an existing project.
  ok("an explicit ls_project on the assistant wins", () => {
    assert.strictEqual(
      traceProject({ metadata: { customer: "Acme" }, context: { ls_project: "my-project" } }),
      "my-project",
    );
    // …but a blank/whitespace one does not.
    assert.strictEqual(
      traceProject({ metadata: { customer: "Acme" }, context: { ls_project: "   " } }),
      "Acme",
    );
  });

  console.log(`\n${passed} trace tests passed.`);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
