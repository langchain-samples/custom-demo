import { useRef, useState } from "react";
import html2pdf from "html2pdf.js";
import { IconDownload } from "@tabler/icons-react";
import type { Widget, KpiWidget, ChartWidget as ChartWidgetSpec, TableWidget as TableWidgetSpec, TextWidget as TextWidgetSpec } from "@/lib/api";
import type { Theme } from "@/lib/theme";
import { resolveColor, toLegacyRgb } from "@/lib/branding";
import { KpiCard } from "@/components/widgets/KpiCard";
import { ChartWidget } from "@/components/widgets/ChartWidget";
import { TableWidget } from "@/components/widgets/TableWidget";
import { TextWidget } from "@/components/widgets/TextWidget";

export interface DashboardCanvasProps {
  /** Validated widget specs, appended progressively as the run streams. */
  widgets: Widget[];
  /** Canvas heading (small uppercase label). Defaults to "Live dashboard". */
  title?: string;
  /** Active theme — recolors chart grids/axes and the exported PDF background. */
  theme?: Theme;
  /** Extra classes for the scroll container. */
  className?: string;
}

/** Single px width of the PDF/print column (fits A4 portrait). */
const PDF_WIDTH = 760;

/** Color functions html2canvas 1.4.1 has no parser for. */
const UNPARSEABLE_COLOR = /color-mix\(|oklch\(|oklab\(|\blab\(|\blch\(|^color\(/;

/**
 * Run `body` with every color token rewritten to a legacy `rgb()`, then put the
 * page back exactly as it was.
 *
 * Without it Download PDF freezes for a moment and then does nothing at all:
 * html2canvas 1.4.1 throws "Attempting to parse an unsupported color function" and
 * rejects. The surfaces in this app are `color-mix(in srgb, ...)` tokens whose
 * computed value serializes as `color(srgb 0.13 0.13 0.15)`, which it cannot read
 * (lib/branding.ts converts the same thing for Chart.js, for the same reason), and
 * `@layer base { * { @apply border-border } }` puts one on the border of every
 * element in the document.
 *
 * Every element in the document, which is the reason this writes to `<html>` rather
 * than to the dashboard: html2pdf clones the dashboard into an `html2pdf__overlay` /
 * `html2pdf__container` pair that it appends to `<body>`, and hands html2canvas the
 * CONTAINER as the render root. Those two divs are created outside the exported
 * subtree, they match `*` like everything else, and parsing the render root is where
 * it throws. An inline declaration on `<html>` beats the `.dark` class rule on the
 * same element, so it reaches them and everything else in one write.
 *
 * Every value written is the one the browser was already painting, so nothing moves
 * on screen while the export runs.
 */
async function withLegacyColors(body: () => Promise<void>): Promise<void> {
  const html = document.documentElement;
  const declared = getComputedStyle(html);
  const tokens = Array.from(declared).filter(
    (name) =>
      name.startsWith("--") && UNPARSEABLE_COLOR.test(declared.getPropertyValue(name).trim()),
  );
  const restore = tokens.map((name) => [name, html.style.getPropertyValue(name)] as const);
  const bodyBackground = document.body.style.backgroundColor;
  try {
    for (const name of tokens) {
      const rgb = resolveColor(name);
      // Skips the non-color tokens (fonts, lengths), which resolve to no color.
      if (/^rgba?\(/.test(rgb)) html.style.setProperty(name, rgb);
    }
    // `body` carries `bg-background`, and html2canvas reads its background off this
    // document before any clone exists, so an inherited token is not enough here.
    const painted = getComputedStyle(document.body).backgroundColor.trim();
    if (UNPARSEABLE_COLOR.test(painted)) document.body.style.backgroundColor = toLegacyRgb(painted);
    await body();
  } finally {
    for (const [name, previous] of restore) {
      if (previous) html.style.setProperty(name, previous);
      else html.style.removeProperty(name);
    }
    document.body.style.backgroundColor = bodyBackground;
  }
}

/**
 * Reflow the dashboard into a single narrow column and save it as a PDF.
 *
 * Separate from the component so the click handler can await it: the promise
 * `.save()` returns was dropped, and every failure inside html2canvas became an
 * unhandled rejection.
 */
async function renderPdf(dash: HTMLElement, theme: Theme): Promise<void> {
  // html2canvas snapshots synchronously: a webfont still in flight would be
  // captured in the fallback face, so the PDF wouldn't match the screen.
  if (document.fonts?.ready) await document.fonts.ready;
  const opt = {
    margin: 8,
    filename: "dashboard.pdf",
    image: { type: "jpeg" as const, quality: 0.98 },
    html2canvas: {
      scale: 2,
      // From the token, so the exported page matches the (possibly
      // brand-tinted) on-screen background instead of a hardcoded grey.
      backgroundColor: resolveColor("--pdf-bg") || (theme === "dark" ? "#0a0a0b" : "#ffffff"),
      useCORS: true,
      windowWidth: PDF_WIDTH,
      onclone: (doc: Document) => {
        // Reveal/pop animations restart in the clone and get snapshotted
        // mid-fade -> washed-out PDF. Force everything fully opaque + static.
        doc.querySelectorAll<HTMLElement>(".canvas-wrap, .widget, .kpi-row, .widget-grid").forEach((n) => {
          n.style.opacity = "1";
          n.style.animation = "none";
          n.style.transform = "none";
          n.style.transition = "none";
        });
        // The on-screen dashboard is 2-column and too wide for A4 portrait,
        // so charts run off the page. Reflow to a single narrow column.
        const d = doc.getElementById("dashboard");
        if (d) {
          d.style.width = PDF_WIDTH + "px";
          d.style.maxWidth = PDF_WIDTH + "px";
        }
        doc.querySelectorAll<HTMLElement>(".widget-grid").forEach((g) => {
          g.style.gridTemplateColumns = "1fr";
        });
        doc.querySelectorAll<HTMLElement>(".span-2").forEach((n) => {
          n.style.gridColumn = "auto";
        });
      },
    },
    jsPDF: { unit: "mm", format: "a4", orientation: "portrait" as const },
    pagebreak: { mode: ["css", "legacy"], avoid: ".widget" },
  };
  await html2pdf().set(opt).from(dash).save();
}

/** Render one non-KPI widget by type. */
function renderWidget(widget: Widget, key: number, theme: Theme) {
  switch (widget.type) {
    case "bar":
    case "line":
    case "pie":
      return <ChartWidget key={key} widget={widget as ChartWidgetSpec} theme={theme} />;
    case "table":
      return <TableWidget key={key} widget={widget as TableWidgetSpec} />;
    case "text":
      return <TextWidget key={key} widget={widget as TextWidgetSpec} />;
    default:
      return null;
  }
}

/**
 * The right-hand "live dashboard" pane. KPI widgets flow into a responsive KPI
 * row; every other widget flows into a 2-column grid (tables/text span both
 * columns). Includes a Download-PDF button that reflows the dashboard to a
 * single 760px column via html2pdf, plus the matching @media print rules.
 *
 * Progressive rendering is driven entirely by the `widgets` prop: the parent
 * appends specs as they stream and React reconciles new cards in.
 */
export function DashboardCanvas({ widgets, title = "Live dashboard", theme = "dark", className }: DashboardCanvasProps) {
  const dashRef = useRef<HTMLDivElement>(null);
  const hasWidgets = widgets.length > 0;
  // Rasterizing the dashboard at scale 2 blocks the main thread for seconds, so the
  // button has to say it is working. Without that the export read as the app hanging.
  const [exporting, setExporting] = useState(false);

  const kpis = widgets.filter((w): w is KpiWidget => w.type === "kpi");
  const rest = widgets.filter((w) => w.type !== "kpi");

  const exportPdf = async () => {
    const dash = dashRef.current;
    if (!dash || !dash.children.length || exporting) return;
    setExporting(true);
    try {
      await withLegacyColors(() => renderPdf(dash, theme));
    } catch (e) {
      // Reported, not swallowed: a click that produces no file and no message is
      // indistinguishable from a dead button.
      console.error("[dashboard] PDF export failed", e);
      window.alert("Could not export the dashboard as a PDF: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className={`canvas-wrap h-full min-h-0 overflow-y-auto px-6 py-5 print:h-auto print:overflow-visible ${className || ""}`}>
      {/* Dashboard-scoped print rules (equivalent to the original @media print). */}
      <style>{`@media print {
        .widget-grid { grid-template-columns: 1fr !important; }
        .span-2 { grid-column: auto !important; }
        .widget { break-inside: avoid; }
        .widget canvas { max-width: 100% !important; height: auto !important; }
      }`}</style>

      <div className="canvas-head mb-3.5 flex items-center justify-between">
        <p className="canvas-title m-0 font-heading text-xs uppercase tracking-wider text-muted-foreground">
          {title}
        </p>
        <button
          type="button"
          onClick={exportPdf}
          disabled={!hasWidgets || exporting}
          title="Download the dashboard as a PDF"
          className="inline-flex items-center gap-1.5 rounded-lg border border-brand bg-transparent px-3.5 py-1.5 text-[13px] font-semibold text-brand transition-colors hover:bg-brand/10 disabled:cursor-default disabled:border-border disabled:text-muted-foreground print:hidden"
        >
          <IconDownload size={16} /> {exporting ? "Preparing PDF..." : "Download PDF"}
        </button>
      </div>

      <div id="dashboard" ref={dashRef}>
        {kpis.length > 0 ? (
          <div className="kpi-row mb-4 grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-3.5">
            {kpis.map((w, i) => (
              <KpiCard key={i} widget={w} />
            ))}
          </div>
        ) : null}
        {rest.length > 0 ? (
          <div className="widget-grid grid grid-cols-2 gap-4">
            {rest.map((w, i) => renderWidget(w, i, theme))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

export default DashboardCanvas;
