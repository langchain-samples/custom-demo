import type { TableWidget as TableWidgetSpec } from "@/lib/api";

/**
 * A data table widget. Spans both grid columns (`.span-2`) like the original.
 */
export function TableWidget({ widget }: { widget: TableWidgetSpec }) {
  return (
    <div className="widget span-2 col-span-2 animate-in rounded-[14px] border border-border bg-panel p-4 shadow-sm duration-300 fade-in slide-in-from-bottom-2">
      <div className="widget-title mb-3 font-heading text-sm font-semibold">{widget.title}</div>
      <table className="data-table w-full border-collapse text-[13px]">
        <thead>
          <tr>
            {widget.columns.map((c, i) => (
              <th
                key={i}
                className="border-b border-border px-2.5 py-2 text-left text-[11px] font-semibold uppercase tracking-wide text-muted-foreground"
              >
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {widget.rows.map((row, ri) => (
            <tr key={ri}>
              {row.map((cell, ci) => (
                <td key={ci} className="border-b border-border px-2.5 py-2 text-left">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
