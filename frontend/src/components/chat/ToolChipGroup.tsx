/**
 * A burst of tool calls, collapsed into one row.
 *
 *   ▸ ⌗ Read a file  +17 steps            Ran command  ⟳
 *
 * A long agent turn produced a wall of rows - eighteen of them in the case this was
 * built for - which buries the two or three calls that actually mattered. Collapsed,
 * the shape of the work reads at a glance and the detail is one click away.
 *
 * Any mix of tools folds together, not just repeats of one. Grouping by tool name split
 * a single stretch of work into a dozen rows as soon as the agent alternated between
 * reading and running, which is most of the time.
 *
 * The header names the FIRST tool and counts the rest, and while the run is live it also
 * shows the tool now in flight, so a collapsed row still narrates progress rather than
 * going quiet for a minute.
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
  const m = toolMeta(chips[0].name);
  const Icon = m.icon;
  // "+N steps" counts the calls BEYOND the one the row names. Now that a run mixes
  // tools those N are not all the same tool, so the row reads "started here, plus N
  // more steps" rather than "N more of this".
  const more = chips.length - 1;
  const running = chips.some((c) => c.result === null && !c.stopped);
  // The tool currently in flight, for the live label. The last chip is the newest, and
  // an agent runs its tools one at a time here.
  const current = running ? toolMeta(chips[chips.length - 1].name).label : "";

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
        <span className="ml-auto flex min-w-0 items-center gap-1.5">
          {running ? (
            <>
              <span className="truncate text-[11px] text-muted-foreground">{current}</span>
              <IconLoader2 size={14} className="shrink-0 animate-spin opacity-70" />
            </>
          ) : (
            <span className="shrink-0 text-[11px] text-muted-foreground">✓ done</span>
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
