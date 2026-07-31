/**
 * Pure chart-series alignment — deliberately dependency-free (only a type import,
 * which is stripped at runtime) so it can be unit-tested directly in Node without
 * a DOM. See dashboard_agent/tests/chart_test.js.
 */
import type { WidgetSeries } from "./api";

/**
 * Align multiple chart series onto ONE category axis.
 *
 * The axis is the UNION of every series' point labels in first-seen order, and
 * each series is placed on it BY LABEL — a value where it has a point for that
 * label, `null` otherwise. This is what lets a short series sit on its real
 * categories: a 4-point forecast for 2026–2029 stays on 2026–2029 and connects
 * to the actuals at the shared anchor year, instead of being left-aligned onto
 * the first four categories (2017–2020) as a plain by-index mapping would do.
 */
export function alignSeries(series: WidgetSeries[]): {
  labels: string[];
  data: (number | null)[][];
} {
  const labels: string[] = [];
  const seen = new Set<string>();
  for (const s of series) {
    for (const p of s.points || []) {
      if (!seen.has(p.label)) {
        seen.add(p.label);
        labels.push(p.label);
      }
    }
  }
  const data = series.map((s) => {
    const byLabel = new Map((s.points || []).map((p) => [p.label, p.value]));
    return labels.map((l) => (byLabel.has(l) ? (byLabel.get(l) as number) : null));
  });
  return { labels, data };
}
