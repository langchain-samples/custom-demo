/* Node test for chart series alignment (pure function, no DOM).
 *
 * Imports the REAL module the app ships (frontend/src/lib/chart.ts) via Node's
 * native type stripping, like branding_test.js. Regression-protects the
 * forecast-on-the-wrong-years bug: a shorter series (e.g. a 3-year forecast)
 * must land on its OWN category labels, not be left-aligned by array index.
 *
 * Run: node dashboard_agent/tests/chart_test.js
 */
const assert = require("node:assert");
const path = require("node:path");

const MOD = path.join(__dirname, "..", "..", "frontend", "src", "lib", "chartAlign.ts");

let passed = 0;
function ok(name, fn) {
  fn();
  passed++;
  console.log("  ok -", name);
}

(async () => {
  const { alignSeries } = await import(MOD);

  ok("shorter series aligns by label, not by index", () => {
    const actual = {
      name: "Actual",
      points: [
        { label: "2024", value: 1 },
        { label: "2025", value: 2 },
        { label: "2026", value: 3 },
      ],
    };
    const forecast = {
      name: "Forecast",
      points: [
        { label: "2026", value: 3 },
        { label: "2027", value: 4 },
        { label: "2028", value: 5 },
      ],
    };
    const { labels, data } = alignSeries([actual, forecast]);

    // Axis is the union in first-seen order.
    assert.deepStrictEqual(labels, ["2024", "2025", "2026", "2027", "2028"]);
    // Actual: values 2024–2026, then null (no forecast years).
    assert.deepStrictEqual(data[0], [1, 2, 3, null, null]);
    // Forecast: null until 2026, then its values — NOT [3,4,5] on 2024–2026.
    // (The old by-index code produced data[1] === [3, 4, 5, null, null].)
    assert.deepStrictEqual(data[1], [null, null, 3, 4, 5]);
  });

  ok("series sharing identical labels are unchanged (no nulls)", () => {
    const a = { name: "A", points: [{ label: "Q1", value: 10 }, { label: "Q2", value: 20 }] };
    const b = { name: "B", points: [{ label: "Q1", value: 5 }, { label: "Q2", value: 8 }] };
    const { labels, data } = alignSeries([a, b]);
    assert.deepStrictEqual(labels, ["Q1", "Q2"]);
    assert.deepStrictEqual(data[0], [10, 20]);
    assert.deepStrictEqual(data[1], [5, 8]);
  });

  ok("single series is a plain value list", () => {
    const { labels, data } = alignSeries([
      { name: "X", points: [{ label: "a", value: 1 }, { label: "b", value: 2 }] },
    ]);
    assert.deepStrictEqual(labels, ["a", "b"]);
    assert.deepStrictEqual(data[0], [1, 2]);
  });

  console.log(`\n${passed} passed`);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
