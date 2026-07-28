/**
 * Chart + formatting helpers ported verbatim (behavior-identical) from the
 * original static/app.js: PALETTE, formatNumber, chartConfig, trendColor,
 * mdToHtml. Kept framework-agnostic so both the React widgets and any tests
 * can import them.
 */
import type { ChartConfiguration } from "chart.js";
import type { Widget, ChartWidget } from "./api";
import type { Theme } from "./theme";

/** Categorical series palette (fallback when no brand colors are set). */
export const PALETTE: string[] = [
  "#0072BC", "#00A651", "#F5A623", "#D0021B",
  "#8E44AD", "#16A085", "#E67E22", "#2C3E50",
];

/** Grid-line + axis-text colors per theme (subtle grid, muted text). */
const CHART_THEME: Record<Theme, { grid: string; text: string }> = {
  light: { grid: "#e4e4e9", text: "#6b6b76" },
  dark: { grid: "#26262b", text: "#8a8a93" },
};

/** Read a CSS custom property off :root (empty string when unset / no DOM). */
function cssVar(name: string): string {
  if (typeof document === "undefined") return "";
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/**
 * Chart palette led by the active assistant's brand colors: primary drives the
 * first series, secondary the second, then the fallback palette. Falls back to
 * PALETTE[0]/[1] when the brand vars aren't set.
 */
export function brandPalette(): string[] {
  const primary = cssVar("--brand-primary") || PALETTE[0];
  const secondary = cssVar("--brand-secondary") || PALETTE[1];
  const rest = PALETTE.filter((c) => c !== primary && c !== secondary);
  return [primary, secondary, ...rest];
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
  const tc = CHART_THEME[theme];

  if (w.type === "pie") {
    const points = series[0]?.points || [];
    return {
      type: "pie",
      data: {
        labels: points.map((p) => p.label),
        datasets: [{
          data: points.map((p) => p.value),
          backgroundColor: points.map((_, i) => pal[i % pal.length]),
          borderColor: theme === "dark" ? "#0f0f11" : "#ffffff",
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

  const labels = (series[0]?.points || []).map((p) => p.label);
  const datasets = series.map((s, i) => ({
    label: s.name,
    data: (s.points || []).map((p) => p.value),
    backgroundColor: w.type === "line" ? "transparent" : pal[i % pal.length],
    borderColor: pal[i % pal.length],
    borderWidth: 2,
    fill: false,
    tension: 0.3,
    pointRadius: 3,
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

/** KPI delta color by trend direction. */
export function trendColor(trend: string | undefined): string {
  return ({ up: "#00A651", down: "#D0021B", flat: "#666" } as Record<string, string>)[trend || ""] || "#666";
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
