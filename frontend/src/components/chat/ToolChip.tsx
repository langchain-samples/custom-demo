/**
 * A collapsible tool-activity "chip" (datasearch / query_sql / write_todos / …).
 * The arg (e.g. the SQL query or search terms) streams in live; once the tool's
 * result arrives the chip becomes clickable to reveal the raw result. Ported
 * from the original `toolChip()` DOM builder.
 */
import { useEffect, useRef, useState } from "react";
import { IconChevronDown, IconChevronRight, IconLoader2 } from "@tabler/icons-react";
import { Streamdown } from "streamdown";
import { toolMeta } from "./helpers";
import { hasToolCard, renderToolResult } from "./ToolResultCard";

export interface ChipData {
  /** tool_call id (or a synthesized fallback) — stable across partials. */
  id: string;
  name: string;
  /** Live arg summary (query text / truncated JSON). */
  arg: string;
  /** Raw tool result, or null until the matching tool message arrives. */
  result: string | null;
  /** Set once the run ends so a still-pending chip stops its spinner/timer. */
  stopped?: boolean;
  /**
   * Source code the tool ran (the `eval` interpreter's `code` arg). When present
   * the expanded chip shows it syntax-highlighted above the output — the raw JSON
   * arg is unreadable and the code is the interesting part.
   */
  code?: string;
}

/** Pretty-print JSON results when possible (matches the original chip). */
function formatResult(content: string): string {
  const text = content || "(empty result)";
  try {
    return JSON.stringify(JSON.parse(content), null, 2);
  } catch {
    return text;
  }
}

export function ToolChip({ chip }: { chip: ChipData }) {
  // Capability tools render a typed card, which is the point of calling them —
  // so those start expanded. Plumbing tools stay collapsed as before.
  const [open, setOpen] = useState(hasToolCard(chip.name));
  const m = toolMeta(chip.name);
  const Icon = m.icon;
  const hasResult = chip.result !== null;
  // "Running" = no result yet AND the run is still live. Once the run ends
  // (chip.stopped) a still-pending chip stops spinning/counting instead of ticking
  // forever — the tool is no longer running, its result just never streamed.
  const running = !hasResult && !chip.stopped;

  // While a tool is running show a live spinner + elapsed seconds, so a long
  // `execute` (pip install, a forecast script) visibly ticks instead of looking
  // frozen. The counter starts when the chip first renders (~tool-call time).
  const startRef = useRef<number>(Date.now());
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!running) return;
    const t = setInterval(
      () => setElapsed(Math.floor((Date.now() - startRef.current) / 1000)),
      1000,
    );
    return () => clearInterval(t);
  }, [running]);
  const arg = chip.arg || "";
  const argText = arg.length > 120 ? arg.slice(0, 120) + "…" : arg;
  // null when there's no renderer for this tool, or the payload is off-shape.
  const card = hasResult ? renderToolResult(chip.name, chip.result as string) : null;

  return (
    <div className="flex animate-in fade-in slide-in-from-bottom-1 flex-col gap-1.5 rounded-lg border border-border bg-panel-2 px-2.5 py-1.5 text-xs text-muted-foreground duration-200">
      <div
        className={
          "flex items-center gap-2" +
          (hasResult ? " cursor-pointer hover:text-brand" : "")
        }
        onClick={() => hasResult && setOpen((v) => !v)}
      >
        <Icon size={15} className="shrink-0" stroke={2} />
        <span className="whitespace-nowrap font-semibold">{m.label}</span>
        {argText && (
          <code className="overflow-hidden text-ellipsis whitespace-nowrap rounded-[5px] border border-border bg-background px-1.5 py-px font-mono text-[11px] text-muted-foreground">
            {argText}
          </code>
        )}
        <span className="ml-auto flex items-center gap-1.5">
          {running ? (
            <>
              {elapsed >= 2 && (
                <span className="tabular-nums text-[11px] text-muted-foreground">{elapsed}s</span>
              )}
              <IconLoader2 size={14} className="animate-spin opacity-70" />
            </>
          ) : hasResult ? (
            open ? (
              <IconChevronDown size={14} />
            ) : (
              <IconChevronRight size={14} />
            )
          ) : null}
        </span>
      </div>
      {hasResult && open && (
        <div className="flex flex-col gap-1.5">
          {/* The code that ran, syntax-highlighted (Streamdown = the chat renderer). */}
          {chip.code && (
            <div className="max-h-72 overflow-auto rounded-md border border-border text-[11px] [&_pre]:!my-0">
              <Streamdown>{"```js\n" + chip.code + "\n```"}</Streamdown>
            </div>
          )}
          {card ?? (
            <pre className="m-0 max-h-60 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border bg-background p-2 font-mono text-[11px] text-foreground">
              {formatResult(chip.result as string)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
