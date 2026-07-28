import type { KpiWidget } from "@/lib/api";
import { trendColor } from "@/lib/chart";

/**
 * A KPI stat card: title, big brand-colored value (+ optional unit), a
 * trend-colored delta, and an optional description. Mirrors the original
 * `.kpi-card` markup from static/app.js.
 */
export function KpiCard({ widget }: { widget: KpiWidget }) {
  return (
    <div className="widget kpi-card flex animate-in flex-col gap-1 rounded-[14px] border border-border bg-panel p-4 shadow-sm duration-300 fade-in slide-in-from-bottom-2">
      <div className="kpi-title text-xs uppercase tracking-wide text-muted-foreground">
        {widget.title}
      </div>
      {/* --brand-primary has a real default in index.css, so no inline fallback. */}
      <div className="kpi-value font-heading text-[30px] font-bold leading-tight text-brand">
        {widget.value}
        {widget.unit ? (
          <span className="kpi-unit text-sm font-medium text-muted-foreground">
            {" "}
            {widget.unit}
          </span>
        ) : null}
      </div>
      {widget.delta ? (
        <div
          className="kpi-delta text-[13px] font-semibold"
          style={{ color: trendColor(widget.trend) }}
        >
          {widget.delta}
        </div>
      ) : null}
      {widget.description ? (
        <div className="kpi-desc text-xs text-muted-foreground">{widget.description}</div>
      ) : null}
    </div>
  );
}
