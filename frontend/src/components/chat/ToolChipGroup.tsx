/**
 * A burst of tool calls, collapsed into one row.
 *
 *   ▸ ⌗ Ran command  +17 steps                          ⟳
 *
 * A long agent turn produced a wall of rows - eighteen of them in the case this was
 * built for - which buries the two or three calls that actually mattered. Collapsed,
 * the shape of the work reads at a glance and the detail is one click away.
 *
 * Any mix of tools folds together, not just repeats of one. Grouping by tool name split
 * a single stretch of work into a dozen rows as soon as the agent alternated between
 * reading and running, which is most of the time.
 *
 * The header names the NEWEST call and counts everything behind it, so a collapsed row
 * narrates progress while the burst grows rather than going quiet for a minute.
 *
 * Chrome deliberately mirrors SubagentCard: same border, chevron, step count and
 * "✓ done". These sit next to each other in the transcript, and a second visual
 * language for "a group of steps you can open" would read as a different kind of thing.
 *
 * Only used for two or more; a lone call stays a plain ToolChip, so nothing has to be
 * expanded to read a turn that called one tool once.
 */
import { useState } from "react";
import { IconChevronDown, IconChevronRight, IconLoader2 } from "@tabler/icons-react";
import { ToolChip, type ChipData } from "./ToolChip";
import { toolMeta } from "./helpers";

export function ToolChipGroup({ chips }: { chips: ChipData[] }) {
  const [open, setOpen] = useState(false);
  // Titled by the LAST call, not the first. Collapsed, the row is a summary of where
  // the work has got to, so the newest step is the useful one - and while the burst is
  // still growing the title narrates progress by itself. Naming the first call also
  // repeated it directly above the identical first row when expanded.
  const newest = chips[chips.length - 1];
  const running = chips.some((c) => c.result === null && !c.stopped);
  // Present tense while the newest call is still in flight, so the row reads "Writing a
  // file" as it happens and "Wrote a file" once it has.
  const m = toolMeta(newest.name, newest.result === null && !newest.stopped);
  const Icon = m.icon;
  // "+N steps" counts everything BEHIND the call the row names.
  const more = chips.length - 1;

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
          {/* No separate "current tool" label any more: the title IS the current tool. */}
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
