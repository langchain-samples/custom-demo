/**
 * A run of adjacent identical tool calls, collapsed into one row.
 *
 *   ▸ ⌗ Ran command  +5 steps                         ✓ done
 *
 * A long agent turn produced a wall of near-identical rows ("Ran command" six times
 * over), which buries the two or three calls that actually mattered. Collapsed, the
 * shape of the work is readable at a glance and the detail is one click away.
 *
 * Chrome deliberately mirrors SubagentCard: same border, same chevron, same step count
 * and the same "✓ done". These sit next to each other in the transcript, and a second
 * visual language for "a group of steps you can open" would read as a different kind of
 * thing.
 *
 * Only used for runs of two or more; a lone call stays a plain ToolChip, so nothing has
 * to be expanded to read a turn that called one tool once.
 */
import { useState } from "react";
import { IconChevronDown, IconChevronRight, IconLoader2 } from "@tabler/icons-react";
import { ToolChip, type ChipData } from "./ToolChip";
import { toolMeta } from "./helpers";

export function ToolChipGroup({ chips }: { chips: ChipData[] }) {
  const [open, setOpen] = useState(false);
  const m = toolMeta(chips[0].name);
  const Icon = m.icon;
  // "+N steps" counts the calls BEYOND the one the row names, which is how the row
  // reads: this tool, plus N more of it.
  const more = chips.length - 1;
  const running = chips.some((c) => c.result === null && !c.stopped);

  return (
    <div className="flex flex-col overflow-hidden rounded-lg border border-border bg-panel-2 text-xs text-muted-foreground">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 px-2.5 py-1.5 text-left hover:text-brand"
      >
        {open ? (
          <IconChevronDown size={14} className="shrink-0" />
        ) : (
          <IconChevronRight size={14} className="shrink-0" />
        )}
        <Icon size={15} className="shrink-0" stroke={2} />
        <span className="truncate font-semibold text-foreground">{m.label}</span>
        <span className="shrink-0 text-[11px] text-muted-foreground">
          +{more} step{more === 1 ? "" : "s"}
        </span>
        <span className="ml-auto flex shrink-0 items-center gap-1.5">
          {running ? (
            <IconLoader2 size={14} className="animate-spin opacity-70" />
          ) : (
            <span className="text-[11px] text-muted-foreground">✓ done</span>
          )}
        </span>
      </button>
      {open && (
        <div className="flex flex-col gap-1.5 border-t border-border p-1.5">
          {chips.map((c) => (
            <ToolChip key={c.id} chip={c} />
          ))}
        </div>
      )}
    </div>
  );
}
