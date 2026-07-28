/**
 * Per-customer brand: seeds in, CSS variables out.
 *
 * Two rules govern this file, and breaking either causes silent, hard-to-see bugs:
 *
 * 1. **JS may only write theme-INDEPENDENT values.** Anything set on
 *    `documentElement.style` is an inline style, so it beats both `:root` and
 *    `.dark` permanently — a theme-dependent value written from JS would be
 *    wrong in one theme with no way for CSS to correct it. Where a value must
 *    differ by theme (the derived chart series), we write BOTH variants
 *    (`--chart-3-light` / `--chart-3-dark`) and let the cascade pick.
 *
 * 2. **Never read a custom property with `getPropertyValue`.** It returns the
 *    token's raw unresolved text — for a derived token that is the literal
 *    string `"color-mix(in srgb, …)"`, which Chart.js and html2canvas cannot
 *    parse and render as black, with no error. Use `resolveColor()`, which
 *    resolves through a real `color` property and normalizes the result.
 *
 *    Note the resolved value is NOT already `rgb()`: browsers serialize a
 *    `color-mix(in srgb, …)` computed value as `color(srgb 0.04 0.04 0.04)`,
 *    which those libraries also cannot parse. `toLegacyRgb()` converts it. Every
 *    token JS reads must therefore be declared with a real default in index.css
 *    and derived in a space `toLegacyRgb` understands (sRGB).
 */
import type { Theme } from "./theme";

/* ------------------------------ Brand shape ----------------------------- */

export interface Brand {
  primary: string;
  secondary: string;
  /** Hue that tints surfaces. Defaults to `primary`. */
  neutral: string;
  /** Surface tint strength, 0–20 (percent). 0 reproduces the original palette. */
  tint: number;
  success?: string;
  warning?: string;
  danger?: string;
}

export const DEFAULT_PRIMARY = "#0072BC";
export const DEFAULT_SECONDARY = "#00A651";
/** Applied when an assistant is active but sets no explicit tint. */
export const DEFAULT_TINT = 6;
export const MAX_TINT = 20;

/* -------------------------------- Hex utils ------------------------------ */

const HEX_RE = /^#?([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/i;

/**
 * Coerce a color to 6-digit `#rrggbb`, or "" when unusable.
 * Brandfetch and the LLM both emit 3-digit and 8-digit forms, which the app's
 * original 6-digit-only regex and `shadeHex` silently rejected.
 */
export function normalizeHex(value: string | undefined | null): string {
  const m = HEX_RE.exec((value || "").trim());
  if (!m) return "";
  let h = m[1];
  if (h.length === 3) h = h.split("").map((c) => c + c).join("");
  if (h.length === 8) h = h.slice(0, 6); // drop alpha
  return "#" + h.toLowerCase();
}

function toRgb(hex: string): [number, number, number] {
  const h = normalizeHex(hex) || DEFAULT_PRIMARY;
  const n = parseInt(h.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function toHex(r: number, g: number, b: number): string {
  const c = (v: number) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0");
  return "#" + c(r) + c(g) + c(b);
}

/* ------------------------------- Contrast -------------------------------- */

/** WCAG relative luminance (sRGB). */
function luminance(hex: string): number {
  const [r, g, b] = toRgb(hex).map((v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG contrast ratio between two colors (1–21). */
export function contrastRatio(a: string, b: string): number {
  const [la, lb] = [luminance(a), luminance(b)];
  const [hi, lo] = la > lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}

/**
 * Black or white, whichever reads better on `bg`.
 * This is what fixes hardcoded `text-white` on a brand fill breaking for light
 * accents (a yellow brand rendered invisible white-on-yellow text).
 */
export function contrastForeground(bg: string): string {
  return contrastRatio("#ffffff", bg) >= contrastRatio("#000000", bg) ? "#ffffff" : "#000000";
}

/* ------------------------------ HSL helpers ------------------------------ */

function toHsl(hex: string): [number, number, number] {
  const [r, g, b] = toRgb(hex).map((v) => v / 255);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  const d = max - min;
  if (d === 0) return [0, 0, l];
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h: number;
  if (max === r) h = ((g - b) / d + (g < b ? 6 : 0)) / 6;
  else if (max === g) h = ((b - r) / d + 2) / 6;
  else h = ((r - g) / d + 4) / 6;
  return [h * 360, s, l];
}

function fromHsl(h: number, s: number, l: number): string {
  h = ((h % 360) + 360) % 360;
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = l - c / 2;
  const seg = Math.floor(h / 60) % 6;
  const [r, g, b] = [
    [c, x, 0], [x, c, 0], [0, c, x], [0, x, c], [x, 0, c], [c, 0, x],
  ][seg];
  return toHex((r + m) * 255, (g + m) * 255, (b + m) * 255);
}

/* --------------------------- Chart palette ------------------------------- */

/** Hue offset + lightness nudge per derived series (indices 3..8). */
const SERIES: Array<[hue: number, dl: number]> = [
  [150, 0], [62, 0.1], [215, -0.08], [285, 0.04], [30, 0.12], [185, -0.12],
];

/**
 * Six derived series colors to sit after the brand pair.
 *
 * Hue offsets are deliberately UNEVEN and lightness alternates: evenly spaced
 * hues off one seed look harmonious but separate poorly, whereas varying
 * lightness as well as hue keeps adjacent series distinguishable (and helps
 * colorblind readers). The saturation floor matters more than it looks — plenty
 * of brands are black or grey, and without it all six would collapse to the
 * same grey.
 */
export function deriveChartPalette(primary: string, theme: Theme): string[] {
  const [h, s, l] = toHsl(normalizeHex(primary) || DEFAULT_PRIMARY);
  // Floor AND ceiling. The floor is the achromatic guard; the ceiling matters
  // just as much — a fully saturated brand (s≈1, e.g. #0072BC) would otherwise
  // rotate into pure #ff0000 / #fff900, which looks garish next to it.
  const sat = Math.min(0.72, Math.max(s, 0.45));
  // Keep every series legible against the panel it sits on.
  const [lo, hi] = theme === "dark" ? [0.5, 0.78] : [0.35, 0.62];
  const base = Math.min(hi, Math.max(lo, l));
  return SERIES.map(([dh, dl]) =>
    fromHsl(h + dh, sat, Math.min(hi, Math.max(lo, base + dl))),
  );
}

/* ------------------------------ resolveColor ----------------------------- */

let probe: HTMLSpanElement | null = null;
const cache = new Map<string, string>();

/**
 * Browsers serialize a `color-mix(in srgb, …)` computed value as
 * `color(srgb 0.039 0.039 0.043)`, NOT as `rgb()` — and Chart.js (@kurkle/color)
 * and html2canvas parse neither `color()` nor `color-mix()`, so they render such
 * a value as black with no error.
 *
 * Converting here, at the single read boundary, keeps the derivation in CSS
 * (where the light/dark cascade does the work) while guaranteeing every consumer
 * sees a legacy `rgb()`/`rgba()` string.
 */
const SRGB_RE = /^color\(srgb\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)(?:\s*\/\s*([\d.eE+-]+))?\s*\)$/;

export function toLegacyRgb(value: string): string {
  const m = SRGB_RE.exec((value || "").trim());
  if (!m) return value;
  const ch = (s: string) => Math.max(0, Math.min(255, Math.round(parseFloat(s) * 255)));
  const [r, g, b] = [ch(m[1]), ch(m[2]), ch(m[3])];
  const a = m[4] === undefined ? 1 : parseFloat(m[4]);
  return a >= 1 ? `rgb(${r}, ${g}, ${b})` : `rgba(${r}, ${g}, ${b}, ${a})`;
}

/**
 * Resolve a CSS custom property to a concrete color string.
 *
 * Sets the token as a real `color` property on a hidden probe and reads the
 * computed value back, which is always fully resolved (`rgb(...)`) whatever the
 * token contains — nested `var()`, `color-mix`, anything.
 */
export function resolveColor(name: string): string {
  const hit = cache.get(name);
  if (hit !== undefined) return hit;
  if (typeof document === "undefined") return "";
  if (!probe) {
    probe = document.createElement("span");
    probe.style.cssText = "position:absolute;width:0;height:0;visibility:hidden";
    document.body.appendChild(probe);
  }
  probe.style.color = "";
  probe.style.color = `var(${name})`;
  const value = toLegacyRgb(getComputedStyle(probe).color);
  if (import.meta.env?.DEV && value && !/^rgba?\(/.test(value)) {
    // Anything still not rgb() here WILL render as black in Chart.js.
    console.warn(
      `[branding] ${name} resolved to "${value}", which Chart.js cannot parse. ` +
        `Derive it in a space toLegacyRgb() understands and give it a default in index.css.`,
    );
  }
  cache.set(name, value);
  return value;
}

/** Drop memoized colors. Call after any brand change or theme flip. */
export function invalidateColorCache(): void {
  cache.clear();
}

/* -------------------------------- applyBrand ----------------------------- */

/** Fill in defaults for a partially specified brand. */
export function brandWithDefaults(b: Partial<Brand> | null | undefined): Brand {
  const primary = normalizeHex(b?.primary) || DEFAULT_PRIMARY;
  return {
    primary,
    secondary: normalizeHex(b?.secondary) || DEFAULT_SECONDARY,
    neutral: normalizeHex(b?.neutral) || primary,
    tint: Math.max(0, Math.min(MAX_TINT, b?.tint ?? DEFAULT_TINT)),
    success: normalizeHex(b?.success) || undefined,
    warning: normalizeHex(b?.warning) || undefined,
    danger: normalizeHex(b?.danger) || undefined,
  };
}

/**
 * Write the brand onto :root. Pass null to clear every override and fall back to
 * the defaults declared in index.css (i.e. the original unbranded palette).
 *
 * Only theme-independent values are written; the derived chart series are
 * written as light AND dark variants so the cascade — not JS — picks.
 */
export function applyBrand(brand: Partial<Brand> | null): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement.style;
  const vars = [
    "--brand-primary", "--brand-secondary", "--brand-neutral", "--brand-tint",
    "--brand-fg", "--success", "--warning", "--danger",
    ...[3, 4, 5, 6, 7, 8].flatMap((i) => [`--chart-${i}-light`, `--chart-${i}-dark`]),
  ];

  if (!brand) {
    vars.forEach((v) => root.removeProperty(v));
    invalidateColorCache();
    return;
  }

  const b = brandWithDefaults(brand);
  root.setProperty("--brand-primary", b.primary);
  root.setProperty("--brand-secondary", b.secondary);
  root.setProperty("--brand-neutral", b.neutral);
  root.setProperty("--brand-tint", `${b.tint}%`);
  root.setProperty("--brand-fg", contrastForeground(b.primary));
  if (b.success) root.setProperty("--success", b.success);
  else root.removeProperty("--success");
  if (b.warning) root.setProperty("--warning", b.warning);
  else root.removeProperty("--warning");
  if (b.danger) root.setProperty("--danger", b.danger);
  else root.removeProperty("--danger");

  const light = deriveChartPalette(b.primary, "light");
  const dark = deriveChartPalette(b.primary, "dark");
  light.forEach((c, i) => root.setProperty(`--chart-${i + 3}-light`, c));
  dark.forEach((c, i) => root.setProperty(`--chart-${i + 3}-dark`, c));

  invalidateColorCache();
}
