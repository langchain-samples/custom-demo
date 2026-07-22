/* Node test for the front-end's pure logic (no browser/DOM needed).
 * Run: node dashboard_agent/tests/frontend_test.js
 */
const assert = require("node:assert");
const path = require("node:path");
const { chartConfig, formatNumber, mdToHtml, trendColor, PALETTE } = require(
  path.join(__dirname, "..", "static", "app.js")
);

let passed = 0;
function ok(name, fn) {
  fn();
  passed++;
  console.log("  ok -", name);
}

ok("formatNumber compacts large numbers", () => {
  assert.strictEqual(formatNumber(2_400_000), "2.4M");
  assert.strictEqual(formatNumber(126_000_000), "126M");
  assert.strictEqual(formatNumber(1_200_000_000), "1.2B");
  assert.strictEqual(formatNumber(68), "68");
});

ok("bar chart config maps series to datasets", () => {
  const cfg = chartConfig({
    type: "bar",
    title: "Funding",
    x_label: "Sector",
    y_label: "USD",
    series: [{ name: "Q2", points: [{ label: "Health", value: 28 }, { label: "WASH", value: 14 }] }],
  });
  assert.strictEqual(cfg.type, "bar");
  assert.deepStrictEqual(cfg.data.labels, ["Health", "WASH"]);
  assert.deepStrictEqual(cfg.data.datasets[0].data, [28, 14]);
});

ok("line chart config supports multi-series", () => {
  const cfg = chartConfig({
    type: "line",
    title: "Trend",
    series: [
      { name: "2025", points: [{ label: "Jan", value: 1 }] },
      { name: "2026", points: [{ label: "Jan", value: 2 }] },
    ],
  });
  assert.strictEqual(cfg.data.datasets.length, 2);
  assert.strictEqual(cfg.options.plugins.legend.display, true);
});

ok("pie chart config uses single dataset with per-slice colors", () => {
  const cfg = chartConfig({
    type: "pie",
    title: "Share",
    series: [{ name: "share", points: [{ label: "A", value: 54 }, { label: "B", value: 28 }] }],
  });
  assert.strictEqual(cfg.type, "pie");
  assert.strictEqual(cfg.data.datasets[0].data.length, 2);
  assert.strictEqual(cfg.data.datasets[0].backgroundColor.length, 2);
});

ok("chartConfig returns null for non-chart widgets", () => {
  assert.strictEqual(chartConfig({ type: "kpi", title: "x", value: "1" }), null);
  assert.strictEqual(chartConfig({ type: "table" }), null);
  assert.strictEqual(chartConfig(null), null);
});

ok("mdToHtml renders bullets and bold", () => {
  const html = mdToHtml("- one\n- two\n**bold**");
  assert.ok(html.includes("<ul>") && html.includes("<li>one</li>"));
  assert.ok(html.includes("<strong>bold</strong>"));
});

ok("mdToHtml escapes html", () => {
  assert.ok(mdToHtml("<script>").includes("&lt;script&gt;"));
});

ok("trendColor maps directions", () => {
  assert.strictEqual(trendColor("up"), "#00A651");
  assert.strictEqual(trendColor("down"), "#D0021B");
  assert.ok(PALETTE.length >= 4);
});

console.log(`\n${passed} frontend tests passed.`);
