/**
 * A tool-activity "chip" (datasearch / query_sql / eval / …), now rendered with
 * beUI's `ToolResult` disclosure as the outer chrome. The streaming reducer in
 * ChatPanel still owns the ChipData model; this is presentation only.
 *
 * The arg (SQL / search terms / first line of code) shows in the header `meta`;
 * once the result arrives the body reveals either a typed capability card
 * (ToolResultCard) or the raw output. Code-running tools (eval/execute) show the
 * source syntax-highlighted via our own CodeView (handles js/bash/python heredocs,
 * which beUI's AgentCode language set doesn't cover).
 */
import { ToolResult, type ToolResultStatus } from "@/components/agents/tool-result";
import { CodeView } from "./CodeView";
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
   * Source/command the tool ran (the `eval` interpreter's `code`, or the sandbox
   * `execute` tool's shell `command`). When present the expanded chip shows it
   * syntax-highlighted above the output — the raw JSON arg is unreadable and the
   * code is the interesting part.
   */
  code?: string;
  /** Fence language for `code` (e.g. "js", "bash"). Defaults to "js". */
  codeLang?: string;
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
  const m = toolMeta(chip.name);
  const Icon = m.icon;
  const hasResult = chip.result !== null;
  const isCode = !!chip.code;

  // Map the chip lifecycle to beUI's ToolResult status. A tool whose result
  // never streamed before the run ended (stopped) is shown as cancelled rather
  // than spinning forever.
  const status: ToolResultStatus = hasResult ? "success" : chip.stopped ? "cancelled" : "running";

  // Capability tools render a typed card, which is the point of calling them —
  // so those start (and stay) expanded. Plumbing tools collapse once complete.
  const cardTool = hasToolCard(chip.name);
  const card = hasResult ? renderToolResult(chip.name, chip.result as string) : null;

  const argText = chip.arg && chip.arg.length > 120 ? chip.arg.slice(0, 120) + "…" : chip.arg;

  return (
    <ToolResult
      icon={<Icon size={13} stroke={2} />}
      title={m.label}
      // The arg summary (query / first code line) goes in the truncating `tool`
      // slot, NOT `meta`: beUI's meta slot is shrink-0 with no truncate, so a
      // long arg there overflowed into the status label (the "squished" chips).
      tool={argText || chip.name}
      status={status}
      kind={isCode ? "terminal" : "custom"}
      defaultOpen={cardTool}
      collapseOnComplete={!cardTool}
      copyText={chip.result ?? chip.code ?? undefined}
    >
      <div className="flex min-w-0 flex-col gap-1.5">
        {/* The code/command that ran, syntax-highlighted (our CodeView; heredoc
            bodies in their own language). Shown as soon as it's known. */}
        {chip.code && <CodeView code={chip.code} lang={chip.codeLang || "js"} />}
        {/* Output: the typed card, else the raw result — only once it has arrived. */}
        {hasResult &&
          (card ?? (
            <pre className="m-0 max-h-60 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border bg-background p-2 font-mono text-[11px] text-foreground">
              {formatResult(chip.result as string)}
            </pre>
          ))}
      </div>
    </ToolResult>
  );
}
