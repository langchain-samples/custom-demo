/**
 * Colours for the voice orb, derived from a brand colour in OKLCH.
 *
 * WHY NOT sRGB MIXING. The first version mixed the brand toward a fixed violet and a fixed
 * pink with `color-mix(in srgb, ...)`. Interpolating between two hues in sRGB cuts a chord
 * through the colour solid that passes near the grey axis, so where two stops overlapped
 * the result desaturated: a blue brand grew a grey-mauve bruise. Rotating HUE in a
 * perceptual space cannot do that, because it travels around the axis rather than across it.
 *
 * The recipe, which is what makes it work for any customer rather than for one:
 *   1. brand colour to OKLCH (lightness, chroma, hue)
 *   2. clamp L and C into a luminous band - this is what saves a near-black brand
 *      (Progressive's own secondary, Nike) and calms an over-saturated one
 *   3. derive the other stops by ROTATING hue, never by mixing toward a fixed colour
 *   4. an almost-white base layer carrying a trace of the brand hue, so gaps between stops
 *      read as light rather than as holes
 *   5. use the brand's real secondary for the third stop only when its hue is far enough
 *      away to add anything; otherwise rotate the other way
 */

/** The luminous band. Outside it an orb is either muddy or a headlight. */
const L_MIN = 0.78;
const L_MAX = 0.86;
const C_MIN = 0.1;
const C_MAX = 0.16;
/** Hue rotation for the derived stops: enough to read as iridescent, not as a rainbow. */
const ROTATION = 26;
/** Below this chroma a colour has no meaningful hue (greys, black, white). */
const ACHROMATIC = 0.02;
/** A secondary closer than this to the primary adds nothing a rotation would not. */
const MIN_SEPARATION = 40;
/** Hue for an achromatic brand, which has none of its own. */
const FALLBACK_HUE = 265;

export interface Oklch {
  l: number;
  c: number;
  h: number;
}

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

function parseHex(hex: string): [number, number, number] {
  let h = (hex || "").trim().replace(/^#/, "");
  if (h.length === 3) h = h.split("").map((ch) => ch + ch).join("");
  if (h.length === 8) h = h.slice(0, 6);
  const n = Number.parseInt(h, 16);
  if (h.length !== 6 || Number.isNaN(n)) return [0, 0, 0];
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

const toLinear = (v: number) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
const toGamma = (v: number) => (v <= 0.0031308 ? v * 12.92 : 1.055 * v ** (1 / 2.4) - 0.055);

/** sRGB hex to OKLCH (Ottosson's OKLab, then polar). */
export function toOklch(hex: string): Oklch {
  const [r, g, b] = parseHex(hex).map(toLinear);
  const l_ = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m_ = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s_ = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  const L = 0.2104542553 * l_ + 0.793617785 * m_ - 0.0040720468 * s_;
  const A = 1.9779984951 * l_ - 2.428592205 * m_ + 0.4505937099 * s_;
  const B = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.808675766 * s_;
  const c = Math.hypot(A, B);
  const h = c < 1e-6 ? 0 : ((Math.atan2(B, A) * 180) / Math.PI + 360) % 360;
  return { l: L, c, h };
}

/** OKLCH back to an sRGB hex, clamped into gamut. */
export function toHex({ l, c, h }: Oklch): string {
  const rad = (h * Math.PI) / 180;
  const A = c * Math.cos(rad);
  const B = c * Math.sin(rad);
  const l_ = (l + 0.3963377774 * A + 0.2158037573 * B) ** 3;
  const m_ = (l - 0.1055613458 * A - 0.0638541728 * B) ** 3;
  const s_ = (l - 0.0894841775 * A - 1.291485548 * B) ** 3;
  const rgb = [
    4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
    -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
    -0.0041960863 * l_ - 0.7034186147 * m_ + 1.707614701 * s_,
  ].map((v) => Math.round(clamp(toGamma(v), 0, 1) * 255));
  return "#" + rgb.map((v) => v.toString(16).padStart(2, "0")).join("");
}

/** Shortest angular distance between two hues, in degrees. */
export function hueGap(a: number, b: number): number {
  const d = Math.abs(((a - b) % 360) + 360) % 360;
  return Math.min(d, 360 - d);
}

export interface OrbPalette {
  /** The brand hue itself, luminous. */
  a: string;
  /** Rotated one way. */
  b: string;
  /** Rotated the other way, or the brand's own secondary when it is far enough off. */
  c: string;
  /** Almost white, faintly tinted: the light the other stops sit in. */
  base: string;
}

/** Three luminous stops plus a base, from a brand colour (and optionally its secondary). */
export function orbPalette(primary: string, secondary?: string): OrbPalette {
  const p = toOklch(primary);
  const s = secondary ? toOklch(secondary) : null;

  /**
   * WHICH COLOUR IS THE BRAND, really. A surprising number of brands store a near-black or
   * grey as their primary and keep the actual colour as the secondary - Progressive's own
   * seeds are `#2d2d2d` and `#0077b3`, and Nike, Apple and Vizient are the same shape. An
   * achromatic primary has no hue to rotate around, so falling back to a house hue would
   * throw away the one colour the customer is actually known for.
   */
  const chromatic = p.c >= ACHROMATIC ? p : s && s.c >= ACHROMATIC ? s : null;
  const hue = chromatic ? chromatic.h : FALLBACK_HUE;
  // Lightness comes from the chromatic source too: a near-black primary would otherwise
  // clamp the whole orb to the bottom of the band and read as a storm cloud.
  const L = clamp(chromatic ? chromatic.l : 0.82, L_MIN, L_MAX);
  const C = clamp(chromatic ? chromatic.c : C_MIN, C_MIN, C_MAX);

  let third = hue - ROTATION;
  // A second real hue earns the third stop, but only if it is far enough from the first to
  // add something a rotation would not - and only if it is not the hue we just took.
  for (const candidate of [p, s]) {
    if (candidate && candidate !== chromatic && candidate.c >= ACHROMATIC) {
      if (hueGap(candidate.h, hue) >= MIN_SEPARATION) third = candidate.h;
    }
  }

  return {
    a: toHex({ l: L, c: C, h: hue }),
    // A touch lighter, so the two rotations do not read as one flat band.
    b: toHex({ l: clamp(L + 0.03, L_MIN, L_MAX + 0.03), c: C, h: hue + ROTATION }),
    c: toHex({ l: L, c: C, h: third }),
    base: toHex({ l: 0.96, c: 0.018, h: hue }),
  };
}
