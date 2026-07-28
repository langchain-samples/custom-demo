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
  const { traceProject, slugifyClient, TRACE_SUFFIX } = await import(MOD);

  ok("project is <client>-corebot-demo", () => {
    assert.strictEqual(
      traceProject({ metadata: { customer: "Acme Health" } }),
      "acme-health-corebot-demo",
    );
    assert.strictEqual(TRACE_SUFFIX, "corebot-demo");
  });

  ok("punctuation and case are slugified", () => {
    assert.strictEqual(slugifyClient("Ben & Jerry's"), "ben-jerry-s");
    assert.strictEqual(slugifyClient("  Spotify  "), "spotify");
    assert.strictEqual(slugifyClient("L'Oréal"), "l-or-al");
    // No leading/trailing or doubled separators.
    assert.ok(!/(^-|-$|--)/.test(slugifyClient("--Acme!!  Health--")));
  });

  ok("falls back through customer -> name -> id", () => {
    assert.strictEqual(traceProject({ name: "Vizient" }), "vizient-corebot-demo");
    assert.strictEqual(traceProject(null, "Globex"), "globex-corebot-demo");
    assert.strictEqual(traceProject(null, ""), "customer-corebot-demo");
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
      "acme-corebot-demo",
    );
  });

  console.log(`\n${passed} trace tests passed.`);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
