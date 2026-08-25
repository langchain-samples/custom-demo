/* Node test for the voice orb's colour derivation (frontend/src/lib/orbPalette.ts).
 *
 * The point of this file is the CORPUS: the orb has to look right for whatever brand a
 * presenter types in, and the two ways it went wrong in practice were both invisible in code.
 * A near-black brand seed produced a storm cloud, and mixing toward a fixed violet/pink in
 * sRGB passed near the grey axis and put a grey bruise on a blue brand's sphere.
 *
 * So every brand below is asserted to land luminous, chromatic and separated - which is the
 * whole claim, "works for any customer", made checkable.
 *
 * Run: node dashboard_agent/tests/orb_palette_test.js
 */
const assert = require("node:assert");
const path = require("node:path");

const MOD = path.join(__dirname, "..", "..", "frontend", "src", "lib", "orbPalette.ts");

let passed = 0;
function ok(name, fn) {
  fn();
  passed++;
  console.log("  ok -", name);
}

/* Real seeds from this repo's own assistants, plus the awkward shapes. Tolerances are wider
   than the band `orbPalette` targets, because the final step clamps into the sRGB gamut and
   a saturated hue loses a little L or C on the way. */
const CORPUS = [
  ["Progressive (near-black primary, blue secondary)", "#2d2d2d", "#0077B3"],
  ["Home Depot (orange)", "#F96302", "#FFFFFF"],
  ["BoA (red + navy)", "#E31837", "#012169"],
  ["Nike (black on black)", "#111111", "#111111"],
  ["Vizient (purple + teal)", "#6D2077", "#00A3AD"],
  ["Walmart (blue + yellow)", "#0071CE", "#FFC220"],
  ["a yellow brand", "#FFD400", ""],
  ["a white-on-white brand", "#FFFFFF", "#FFFFFF"],
];

(async () => {
  const { orbPalette, toOklch, toHex, hueGap } = await import(MOD);

  ok("every brand in the corpus lands luminous, chromatic and separated", () => {
    for (const [label, primary, secondary] of CORPUS) {
      const p = orbPalette(primary, secondary || undefined);
      const stops = [p.a, p.b, p.c].map(toOklch);
      for (const [i, s] of stops.entries()) {
        assert.ok(s.l > 0.72 && s.l < 0.9, `${label} stop ${i}: L ${s.l.toFixed(2)} not luminous`);
        // Chromatic: this is the assertion that catches a grey orb, which is what a
        // black-seeded brand produced before the achromatic fallback existed.
        assert.ok(s.c > 0.06, `${label} stop ${i}: C ${s.c.toFixed(3)} is grey`);
        assert.ok(s.c < 0.2, `${label} stop ${i}: C ${s.c.toFixed(3)} is garish`);
      }
      // Separated, or the three stops read as one flat band.
      assert.ok(hueGap(stops[0].h, stops[1].h) >= 15, `${label}: stops 0/1 too close`);
      assert.ok(hueGap(stops[0].h, stops[2].h) >= 15, `${label}: stops 0/2 too close`);
      // The base is the light the stops sit in: near-white, barely tinted.
      const base = toOklch(p.base);
      assert.ok(base.l > 0.92, `${label}: base is not near-white`);
      assert.ok(base.c < 0.05, `${label}: base is too saturated to read as light`);
    }
  });

  ok("an achromatic primary borrows the hue from the secondary", () => {
    // Progressive's real seeds. Their brand IS blue; it just is not in the primary slot, and
    // falling back to a house hue would throw away the one colour they are known for.
    const p = orbPalette("#2d2d2d", "#0077B3");
    const lead = toOklch(p.a);
    const blue = toOklch("#0077B3");
    assert.ok(hueGap(lead.h, blue.h) < 20, `expected the brand blue, got hue ${Math.round(lead.h)}`);
  });

  ok("with no chromatic seed at all it still produces colour", () => {
    // Nike-on-Nike: nothing to borrow, so a house hue is the honest answer - three greys
    // would look broken rather than minimal.
    for (const stop of [orbPalette("#111111").a, orbPalette("#ffffff", "#eeeeee").b]) {
      assert.ok(toOklch(stop).c > 0.06, `${stop} is grey`);
    }
  });

  ok("a distinct secondary earns the third stop", () => {
    // Walmart's yellow is far from its blue, so it should appear rather than being replaced
    // by a rotation of the blue.
    const p = orbPalette("#0071CE", "#FFC220");
    assert.ok(hueGap(toOklch(p.c).h, toOklch("#FFC220").h) < 25, "yellow should reach the orb");
    // Home Depot's secondary is white: nothing to contribute, so the third stop is a
    // rotation of the primary and stays in the orange family.
    const hd = orbPalette("#F96302", "#FFFFFF");
    assert.ok(hueGap(toOklch(hd.c).h, toOklch("#F96302").h) <= 30);
  });

  ok("hue rotation never crosses the grey axis", () => {
    // The original bug, as a property: mixing blue toward pink in sRGB desaturates through
    // the middle. Rotation cannot, so sampling the arc between two stops stays chromatic.
    const from = toOklch("#0077B3");
    for (let deg = 0; deg <= 60; deg += 10) {
      const mid = toHex({ l: 0.82, c: 0.13, h: from.h + deg });
      assert.ok(toOklch(mid).c > 0.06, `${mid} at +${deg} deg lost its chroma`);
    }
  });

  ok("hex parsing survives the shapes a brand fetch returns", () => {
    for (const input of ["#0077B3", "0077b3", "#07b", "#0077B3FF", "", "not-a-colour"]) {
      const p = orbPalette(input, "#0077B3");
      assert.ok(/^#[0-9a-f]{6}$/.test(p.a), `${input} produced ${p.a}`);
    }
  });

  console.log(`\n${passed} passed`);
})();
