/**
 * Chart + formatting helpers ported verbatim (behavior-identical) from the
 * original static/app.js: PALETTE, formatNumber, chartConfig, trendColor,
 * mdToHtml. Kept framework-agnostic so both the React widgets and any tests
 * can import them.
 */
import type { ChartConfiguration } from "chart.js";
import type { Widget, ChartWidget } from "./api";
import type { Theme } from "./theme";
import { resolveColor } from "./branding";
import { alignSeries } from "./chartAlign";

/**
 * Categorical series palette used only when there is no DOM to resolve tokens
 * against (Node tests). In the browser every color comes from the `--chart-*`
 * tokens, which derive from the active brand — see lib/branding.ts.
 */
export const PALETTE: string[] = [
  "#0072BC", "#00A651", "#F5A623", "#D0021B",
  "#8E44AD", "#16A085", "#E67E22", "#2C3E50",
];

/**
 * The eight categorical series colors for the active brand and theme.
 *
 * Resolved through `resolveColor` rather than read as raw custom properties:
 * these tokens are `color-mix`/`var()` chains whose raw text Chart.js cannot
 * parse (it would render them black).
 */
export function brandPalette(): string[] {
  return PALETTE.map((fallback, i) => resolveColor(`--chart-${i + 1}`) || fallback);
}

/** Compact human number: 1_500 -> "1.5K", 2_000_000 -> "2M". */
export function formatNumber(n: number): string {
  if (typeof n !== "number" || !isFinite(n)) return String(n);
  const abs = Math.abs(n);
  if (abs >= 1_000_000_000) return (n / 1_000_000_000).toFixed(1).replace(/\.0$/, "") + "B";
  if (abs >= 1_000_000) return (n / 1_000_000).toFixed(1).replace(/\.0$/, "") + "M";
  if (abs >= 1_000) return (n / 1_000).toFixed(1).replace(/\.0$/, "") + "K";
  return String(n);
}

/**
 * Turn a widget spec into a Chart.js config for bar/line/pie widgets.
 * Returns null for any other widget type. Pure.
 */
export function chartConfig(
  widget: Widget | null | undefined,
  theme: Theme = "dark",
): ChartConfiguration | null {
  if (!widget || !["bar", "line", "pie"].includes(widget.type)) return null;
  const w = widget as ChartWidget;
  const series = w.series || [];
  const pal = brandPalette();
  // Chart chrome from tokens, so it can never drift from the surface palette the
  // way the old hardcoded values had. `theme` still matters: it is what makes
  // React re-run this on a light/dark flip (the tokens themselves live in CSS).
  const tc = {
    grid: resolveColor("--chart-grid") || (theme === "dark" ? "#26262b" : "#e4e4e9"),
    text: resolveColor("--chart-text") || (theme === "dark" ? "#8a8a93" : "#6b6b76"),
    slice: resolveColor("--chart-slice-border") || (theme === "dark" ? "#0f0f11" : "#ffffff"),
  };

  if (w.type === "pie") {
    const points = series[0]?.points || [];
    return {
      type: "pie",
      data: {
        labels: points.map((p) => p.label),
        datasets: [{
          data: points.map((p) => p.value),
          backgroundColor: points.map((_, i) => pal[i % pal.length]),
          borderColor: tc.slice,
          borderWidth: 2,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: "right", labels: { color: tc.text } } },
      },
    } as ChartConfiguration;
  }

  const { labels, data: aligned } = alignSeries(series);
  const datasets = series.map((s, i) => ({
    label: s.name,
    data: aligned[i],
    backgroundColor: w.type === "line" ? "transparent" : pal[i % pal.length],
    borderColor: pal[i % pal.length],
    borderWidth: 2,
    fill: false,
    tension: 0.3,
    pointRadius: 3,
    spanGaps: false, // don't bridge a series across labels it doesn't cover
  }));

  return {
    type: w.type,
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: series.length > 1, labels: { color: tc.text } },
      },
      scales: {
        x: {
          title: { display: !!w.x_label, text: w.x_label || "", color: tc.text },
          ticks: { color: tc.text },
          grid: { color: tc.grid },
          border: { color: tc.grid },
        },
        y: {
          title: { display: !!w.y_label, text: w.y_label || "", color: tc.text },
          ticks: {
            color: tc.text,
            callback: (v: string | number) => formatNumber(typeof v === "number" ? v : Number(v)),
          },
          grid: { color: tc.grid },
          border: { color: tc.grid },
        },
      },
    },
  } as ChartConfiguration;
}

/** KPI delta color by trend direction, from the semantic tokens. */
export function trendColor(trend: string | undefined): string {
  const token = ({ up: "--success", down: "--danger" } as Record<string, string>)[trend || ""];
  const fallback = ({ up: "#00A651", down: "#D0021B" } as Record<string, string>)[trend || ""] || "#666";
  return (token && resolveColor(token)) || fallback;
}

/**
 * Minimal markdown -> HTML (paragraphs, `-`/`*` bullet lists, **bold**),
 * with HTML escaping first. Matches the original renderer exactly.
 */
export function mdToHtml(md: string): string {
  const escaped = String(md).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const lines = escaped.split("\n");
  let html = "";
  let inList = false;
  for (const line of lines) {
    const t = line.trim();
    if (/^[-*]\s+/.test(t)) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += "<li>" + t.replace(/^[-*]\s+/, "") + "</li>";
    } else {
      if (inList) { html += "</ul>"; inList = false; }
      if (t) html += "<p>" + t + "</p>";
    }
  }
  if (inList) html += "</ul>";
  return html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
}
