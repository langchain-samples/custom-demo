import { Chart as ChartJS, registerables } from "chart.js";
import { Chart } from "react-chartjs-2";
import type { ChartWidget as ChartWidgetSpec } from "@/lib/api";
import type { Theme } from "@/lib/theme";
import { chartConfig } from "@/lib/chart";

// Register every controller/element/scale. Grid/axis colors are set per-config
// (theme-aware) in chartConfig(), so no global dark defaults here.
ChartJS.register(...registerables);

/**
 * A bar / line / pie widget. The Chart.js config is produced by the shared,
 * behavior-identical `chartConfig()` helper (PALETTE, formatNumber ticks, etc.)
 * and rendered inside a fixed-height wrapper so the responsive canvas has a box
 * to fill. `theme` recolors the grid/axis text for light vs dark.
 */
export function ChartWidget({ widget, theme = "dark" }: { widget: ChartWidgetSpec; theme?: Theme }) {
  const config = chartConfig(widget, theme);
  if (!config) return null;
  return (
    <div className="widget animate-in rounded-[14px] border border-border bg-panel p-4 shadow-sm duration-300 fade-in slide-in-from-bottom-2">
      <div className="widget-title mb-3 font-heading text-sm font-semibold">{widget.title}</div>
      <div className="chart-wrap relative h-[260px]">
        <Chart type={config.type} data={config.data} options={config.options} />
      </div>
    </div>
  );
}
