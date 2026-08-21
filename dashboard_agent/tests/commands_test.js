/* Node test for the composer's slash commands (pure functions).
 *
 * Imports the REAL module the app ships (frontend/src/lib/commands.ts) via Node's
 * native type stripping, like stream_event_test.js. Guards the one thing that is
 * easy to get wrong and invisible when it breaks: which typed strings set a goal
 * and which are just questions.
 *
 * Run: node dashboard_agent/tests/commands_test.js
 */
const assert = require("node:assert");
const path = require("node:path");

const MOD = path.join(__dirname, "..", "..", "frontend", "src", "lib", "commands.ts");

let passed = 0;
function ok(name, fn) {
  fn();
  passed++;
  console.log("  ok -", name);
}

(async () => {
  const { COMMANDS, parseGoalCommand } = await import(MOD);

  ok("the palette offers /goal", () => {
    assert.ok(COMMANDS.some((c) => c.name === "/goal" && c.hint));
  });

  ok("a leading /goal sets the objective", () => {
    assert.deepStrictEqual(parseGoalCommand("/goal build me a transactions dashboard"), {
      kind: "set",
      text: "build me a transactions dashboard",
    });
  });

  ok("/goal mid-sentence still sets it", () => {
    // How people actually type it. Anchoring only at the start sent this through
    // as an ordinary question and the goal was never set.
    assert.deepStrictEqual(
      parseGoalCommand("set a /goal to build me a dashboard for my transactions"),
      { kind: "set", text: "to build me a dashboard for my transactions" },
    );
  });

  ok("bare /goal, show and clear are inspection, not objectives", () => {
    assert.deepStrictEqual(parseGoalCommand("/goal"), { kind: "show" });
    assert.deepStrictEqual(parseGoalCommand("/goal show"), { kind: "show" });
    assert.deepStrictEqual(parseGoalCommand("/goal  Clear "), { kind: "clear" });
  });

  ok("an ordinary question is not a command", () => {
    assert.strictEqual(parseGoalCommand("what were sales last month?"), null);
    assert.strictEqual(parseGoalCommand("what is our goal for Q3?"), null);
    // A word that merely starts the same way is a different command, not this one.
    assert.strictEqual(parseGoalCommand("/goalie stats"), null);
  });

  ok("multi-line objectives keep their body", () => {
    assert.deepStrictEqual(parseGoalCommand("/goal ship it\nwith tests"), {
      kind: "set",
      text: "ship it\nwith tests",
    });
  });

  console.log(`\n${passed} passed`);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
