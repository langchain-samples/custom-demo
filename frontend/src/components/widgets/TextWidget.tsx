import type { TextWidget as TextWidgetSpec } from "@/lib/api";
import { mdToHtml } from "@/lib/chart";

/**
 * A "Key findings" / free-text widget. Content is rendered through the shared
 * minimal-markdown converter (paragraphs, bullet lists, **bold**) with the same
 * escaping the original used. Spans both grid columns (`.span-2`).
 */
export function TextWidget({ widget }: { widget: TextWidgetSpec }) {
  return (
    <div className="widget span-2 col-span-2 animate-in rounded-[14px] border border-border bg-panel p-4 shadow-sm duration-300 fade-in slide-in-from-bottom-2">
      <div className="widget-title mb-3 text-sm font-semibold">{widget.title}</div>
      <div
        className="text-body text-[13.5px] leading-[1.55] [&_p]:my-1 [&_strong]:font-semibold [&_ul]:my-1.5 [&_ul]:list-disc [&_ul]:pl-[18px]"
        dangerouslySetInnerHTML={{ __html: mdToHtml(widget.content) }}
      />
    </div>
  );
}
