/**
 * The newest few tool calls of a burst, as a vertical timeline.
 *
 *   ⌗  Ran command     uv run pytest
 *   │
 *   ⌕  Searched files  "deadline"
 *   │
 *   ···  Reading a file  data/orders.csv
 *
 * One line per call with its icon, joined by a thin rule, so the last few steps of a
 * turn read at a glance without opening anything. Everything older than these folds into
 * a ToolChipGroup above (see ToolActivityView), so the transcript never grows a wall of
 * rows however long the turn runs.
 *
 * A call still in flight shows animated dots in place of its icon and the present tense,
 * which is the whole "what is it doing right now" signal while the stream runs.
 *
 * Each row opens onto the same detail a ToolChip shows (code, typed card, raw output).
 * Capability tools start open, as they do in ToolChip: their card is the point of the call.
 */
import { useState } from "react";
import { IconChevronDown, IconChevronRight } from "@tabler/icons-react";
import { ToolChipBody, type ChipData } from "./ToolChip";
import { ToolChipGroup } from "./ToolChipGroup";
import { toolMeta } from "./helpers";
import { hasToolCard } from "./ToolResultCard";

/** How many of the newest calls stay visible as timeline rows. */
export const TIMELINE_VISIBLE = 3;

/** A burst of tool calls: older ones collapsed into one row, the newest as a timeline. */
export function ToolActivityView({ chips }: { chips: ChipData[] }) {
  const older = chips.slice(0, -TIMELINE_VISIBLE);
  const recent = chips.slice(-TIMELINE_VISIBLE);
  return (
    <div className="flex flex-col gap-2">
      {/* Keyed on the first chip so the row keeps whether you opened it as calls
          scroll out of the timeline and into it. */}
      {older.length > 0 && <ToolChipGroup key={older[0].id} chips={older} />}
      <ToolTimeline chips={recent} />
    </div>
  );
}

export function ToolTimeline({ chips }: { chips: ChipData[] }) {
  return (
    <ol className="m-0 flex list-none flex-col p-0 pl-1">
      {chips.map((chip, i) => (
        <TimelineRow key={chip.id} chip={chip} last={i === chips.length - 1} />
      ))}
    </ol>
  );
}

function TimelineRow({ chip, last }: { chip: ChipData; last: boolean }) {
  const running = chip.result === null && !chip.stopped;
  const [open, setOpen] = useState(() => hasToolCard(chip.name));
  const m = toolMeta(chip.name, running);
  const Icon = m.icon;
  const hasDetail = chip.result !== null || !!chip.code;
  const arg = chip.arg && chip.arg.length > 120 ? chip.arg.slice(0, 120) + "…" : chip.arg;

  return (
    <li className="relative flex gap-2.5">
      {/* The rule joining this icon to the next one. */}
      {!last && (
        <span aria-hidden className="absolute top-6 bottom-0 left-[9px] w-px bg-border" />
      )}
      <span className="flex h-6 w-5 shrink-0 items-center justify-center text-muted-foreground">
        {running ? <RunningDots /> : <Icon size={16} stroke={1.75} />}
      </span>
      <div className={`flex min-w-0 flex-1 flex-col ${last ? "" : "pb-3"}`}>
        <button
          type="button"
          disabled={!hasDetail}
          onClick={() => setOpen((v) => !v)}
          className="group flex h-6 min-w-0 items-center gap-2 text-left text-[13px] enabled:hover:text-brand"
        >
          <span
            className={`shrink-0 ${running ? "text-muted-foreground" : "text-foreground group-enabled:group-hover:text-brand"}`}
          >
            {m.label}
            {running && "…"}
          </span>
          {arg && <span className="truncate text-xs text-muted-foreground">{arg}</span>}
          {hasDetail &&
            (open ? (
              <IconChevronDown size={13} className="shrink-0 text-muted-foreground" />
            ) : (
              <IconChevronRight
                size={13}
                className="shrink-0 text-muted-foreground opacity-0 group-hover:opacity-100"
              />
            ))}
        </button>
        {open && hasDetail && (
          <div className="mt-1.5">
            <ToolChipBody chip={chip} />
          </div>
        )}
      </div>
    </li>
  );
}

/** Three staggered pulsing dots, the in-flight marker in place of a tool icon. */
function RunningDots() {
  return (
    <span role="status" aria-label="Running" className="flex items-center gap-[3px] text-brand">
      {[0, 150, 300].map((delay) => (
        <span
          key={delay}
          className="size-1 animate-pulse rounded-full bg-current"
          style={{ animationDelay: `${delay}ms` }}
        />
      ))}
    </span>
  );
}
