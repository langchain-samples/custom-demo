/* Node test for the branding colour maths (pure functions, no DOM).
 *
 * Imports the REAL module the app ships (frontend/src/lib/branding.ts) via Node's
 * native type stripping — unlike frontend_test.js, which still tests the legacy
 * static/app.js copy.
 *
 * Run: node dashboard_agent/tests/branding_test.js
 */
const assert = require("node:assert");
const path = require("node:path");

const MOD = path.join(__dirname, "..", "..", "frontend", "src", "lib", "branding.ts");

let passed = 0;
function ok(name, fn) {
  fn();
  passed++;
  console.log("  ok -", name);
}

(async () => {
  const {
    normalizeHex,
    contrastRatio,
    contrastForeground,
    deriveChartPalette,
    brandWithDefaults,
    toLegacyRgb,
    DEFAULT_PRIMARY,
    DEFAULT_TINT,
    MAX_TINT,
  } = await import(MOD);

  // Regression: browsers serialize color-mix(in srgb, …) as `color(srgb …)`,
  // which Chart.js/html2canvas render as BLACK. resolveColor must convert it.
  ok("toLegacyRgb converts color(srgb …) to rgb()", () => {
    assert.strictEqual(toLegacyRgb("color(srgb 0.0392157 0.0392157 0.0431373)"), "rgb(10, 10, 11)");
    assert.strictEqual(toLegacyRgb("color(srgb 1 0 0)"), "rgb(255, 0, 0)");
    assert.strictEqual(toLegacyRgb("color(srgb 0 0 0 / 0.5)"), "rgba(0, 0, 0, 0.5)");
    // Already-legacy values and anything unrecognized pass through untouched.
    assert.strictEqual(toLegacyRgb("rgb(1, 2, 3)"), "rgb(1, 2, 3)");
    assert.strictEqual(toLegacyRgb("oklch(0.7 0.1 200)"), "oklch(0.7 0.1 200)");
  });

  ok("normalizeHex expands 3-digit and strips alpha", () => {
    assert.strictEqual(normalizeHex("#abc"), "#aabbcc");
    assert.strictEqual(normalizeHex("0072BC"), "#0072bc");
    assert.strictEqual(normalizeHex("#0072BCFF"), "#0072bc");
    assert.strictEqual(normalizeHex("rebeccapurple"), "");
    assert.strictEqual(normalizeHex(undefined), "");
  });

  ok("contrastRatio matches known WCAG values", () => {
    assert.strictEqual(Math.round(contrastRatio("#000000", "#ffffff")), 21);
    assert.strictEqual(Math.round(contrastRatio("#ffffff", "#ffffff")), 1);
  });

  // The bug this whole token replaces: hardcoded white on a light brand fill.
  ok("contrastForeground flips to black on light brands", () => {
    assert.strictEqual(contrastForeground("#0072BC"), "#ffffff"); // dark blue
    assert.strictEqual(contrastForeground("#FFC220"), "#000000"); // Walmart yellow
    assert.strictEqual(contrastForeground("#ffffff"), "#000000");
    assert.strictEqual(contrastForeground("#000000"), "#ffffff");
  });

  ok("contrastForeground always clears 4.5:1", () => {
    for (const bg of ["#0072BC", "#FFC220", "#00A651", "#D0021B", "#8E44AD", "#2C3E50"]) {
      const fg = contrastForeground(bg);
      assert.ok(contrastRatio(fg, bg) >= 4.5, `${fg} on ${bg} = ${contrastRatio(fg, bg)}`);
    }
  });

  ok("deriveChartPalette returns 6 distinct valid hexes", () => {
    const pal = deriveChartPalette("#0072BC", "dark");
    assert.strictEqual(pal.length, 6);
    pal.forEach((c) => assert.match(c, /^#[0-9a-f]{6}$/));
    assert.strictEqual(new Set(pal).size, 6, "series must be distinguishable");
  });

  // Plenty of brands are black/grey; without a saturation floor every derived
  // series collapses to the same grey.
  ok("achromatic brands still produce a varied palette", () => {
    for (const brand of ["#000000", "#ffffff", "#808080"]) {
      const pal = deriveChartPalette(brand, "light");
      assert.strictEqual(new Set(pal).size, 6, `${brand} collapsed to ${new Set(pal).size}`);
    }
  });

  // A fully saturated brand (s≈1) would otherwise rotate into pure #ff0000 /
  // #fff900, which looks garish beside it.
  ok("saturated brands do not produce garish series", () => {
    for (const c of deriveChartPalette("#0072BC", "dark")) {
      const [r, g, b] = [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16));
      const max = Math.max(r, g, b), min = Math.min(r, g, b);
      const sat = max === 0 ? 0 : (max - min) / max;
      assert.ok(sat <= 0.85, `${c} is over-saturated (${sat.toFixed(2)})`);
    }
  });

  ok("palette lightness stays in the legible band per theme", () => {
    // Rough luminance proxy: no series should be near-black or near-white.
    for (const theme of ["light", "dark"]) {
      for (const c of deriveChartPalette("#0072BC", theme)) {
        const n = parseInt(c.slice(1), 16);
        const avg = (((n >> 16) & 255) + ((n >> 8) & 255) + (n & 255)) / 3;
        assert.ok(avg > 25 && avg < 240, `${c} out of band in ${theme}`);
      }
    }
  });

  ok("brandWithDefaults fills gaps and clamps tint", () => {
    const b = brandWithDefaults(null);
    assert.strictEqual(b.primary, DEFAULT_PRIMARY);
    assert.strictEqual(b.tint, DEFAULT_TINT);
    // neutral follows primary unless set
    assert.strictEqual(brandWithDefaults({ primary: "#ff0000" }).neutral, "#ff0000");
    assert.strictEqual(brandWithDefaults({ tint: 999 }).tint, MAX_TINT);
    assert.strictEqual(brandWithDefaults({ tint: -5 }).tint, 0);
    // garbage colours fall back rather than propagating
    assert.strictEqual(brandWithDefaults({ primary: "nonsense" }).primary, DEFAULT_PRIMARY);
  });

  console.log(`\n${passed} branding tests passed.`);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
