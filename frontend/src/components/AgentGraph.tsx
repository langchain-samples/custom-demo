/**
 * Ported from the Super Group build (joel-langchain, 9115f8a), where it was marked
 * demo-local. It is a pure renderer over state the chat rail already holds, so there is
 * nothing customer-specific in it and every assistant gets something out of it.
 *
 * "Graph mode": a live, left-to-right activity graph of the deep agent working,
 * in the spirit of LangGraph Studio's graph view but driven by the SAME stream
 * the chat rail already consumes. It is a RENDERER over existing state — it adds
 * no requests, no stream handling, and no coupling to the dashboard path, so
 * turning it on cannot change what the agent does.
 *
 * Nodes light up as calls arrive (dashed + pulsing while pending, solid once the
 * tool result lands) and each is clickable to see the argument and the result.
 * Subagent lanes appear only when the agent actually dispatches `task`, which
 * needs DA_DYNAMIC_SUBAGENTS=1 on the server; without it you get the single-lane
 * view, which is still the real picture: plan, skills, sandbox, data, output.
 *
 * This file exports ONLY components. The pure helpers live in `lib/agentGraph.ts`
 * because a mixed export breaks Fast Refresh and makes Vite full-reload on edit,
 * which wipes the chat mid-run.
 */

import { useMemo, useState } from "react";

import type { ChipData } from "@/components/chat/ToolChip";
import {
  LANES,
  LANE_GAP,
  LANE_W,
  LANE_X,
  NODE_W,
  NODE_X,
  ROOT_W,
  ROOT_X,
  ROW_H,
  edge,
  isPending as pending,
  laneFor,
  nodeLabel,
  resultSize,
  type GraphSubagent,
} from "@/lib/agentGraph";

export type { GraphSubagent };

export interface AgentGraphProps {
  chips: ChipData[];
  subagents: GraphSubagent[];
  /** True while the run is streaming — drives the root node's pulse. */
  running: boolean;
  /** Assistant display name for the root node. */
  name: string;
}

/* ---- Component ----------------------------------------------------------- */

export function AgentGraph({ chips, subagents, running, name }: AgentGraphProps) {
  const [selected, setSelected] = useState<string | null>(null);

  // Group calls into lanes, keeping arrival order within each lane. Only lanes
  // that actually fired are drawn, so the graph stays honest about what ran.
  const lanes = useMemo(() => {
    const byLane = new Map<string, ChipData[]>();
    for (const c of chips) {
      const l = laneFor(c);
      const list = byLane.get(l);
      if (list) list.push(c);
      else byLane.set(l, [c]);
    }
    return LANES.filter((l) => byLane.has(l.id)).map((l) => ({
      ...l,
      calls: byLane.get(l.id)!,
    }));
  }, [chips]);

  // Lay lanes out top to bottom; each lane is as tall as its call count.
  const laid = useMemo(() => {
    let y = 20;
    const out = lanes.map((l) => {
      const h = Math.max(1, l.calls.length) * ROW_H;
      const box = { ...l, y, h, cy: y + h / 2 };
      y += h + LANE_GAP;
      return box;
    });
    return { rows: out, height: Math.max(180, y + 10) };
  }, [lanes]);

  const allChips = useMemo(
    () => [...chips, ...subagents.flatMap((s) => s.chips)],
    [chips, subagents],
  );
  const openChip = allChips.find((c) => c.id === selected) || null;

  const doneCount = chips.filter((c) => !pending(c)).length;

  if (!chips.length && !subagents.length) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
        <div className="font-heading text-lg font-semibold">Agent graph</div>
        <p className="max-w-[36ch] text-sm text-muted-foreground">
          Ask a question and the agent&apos;s work draws itself here: the plan it writes, the
          skills it consults, its sandbox, the systems it queries, and what it puts on the
          dashboard.
        </p>
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="flex flex-shrink-0 items-center gap-3 px-5 py-3.5">
        <span className="text-[11px] font-semibold tracking-[0.14em] text-muted-foreground uppercase">
          Agent graph
        </span>
        <span className="text-xs text-muted-foreground">
          {doneCount}/{chips.length} steps
          {subagents.length ? ` · ${subagents.length} subagent${subagents.length > 1 ? "s" : ""}` : ""}
        </span>
        {running && (
          <span className="flex items-center gap-1.5 text-xs text-[var(--brand-primary)]">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--brand-primary)]" />
            running
          </span>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-auto px-5 pb-5">
        <svg
          width={NODE_X + NODE_W + 16}
          height={laid.height}
          className="max-w-none"
          role="img"
          aria-label="Live graph of the agent's activity"
        >
          {/* Edges first, so nodes paint over them. */}
          <g fill="none">
            {laid.rows.map((lane) => (
              <path
                key={`e-root-${lane.id}`}
                d={edge(ROOT_X + ROOT_W, 40, LANE_X, lane.cy)}
                stroke="var(--border)"
                strokeWidth={1.5}
              />
            ))}
            {laid.rows.map((lane) =>
              lane.calls.map((c, i) => (
                <path
                  key={`e-${c.id}`}
                  d={edge(LANE_X + LANE_W, lane.cy, NODE_X, lane.y + i * ROW_H + ROW_H / 2)}
                  stroke={pending(c) ? "var(--brand-primary)" : "var(--border)"}
                  strokeWidth={1.5}
                  strokeDasharray={pending(c) ? "4 3" : undefined}
                  opacity={pending(c) ? 0.9 : 1}
                />
              )),
            )}
          </g>

          {/* Root: the agent itself. */}
          <g>
            <rect
              x={ROOT_X}
              y={22}
              width={ROOT_W}
              height={36}
              rx={10}
              fill="var(--brand-primary)"
              opacity={running ? 1 : 0.85}
            />
            <text
              x={ROOT_X + 14}
              y={45}
              fill="var(--brand-fg)"
              className="font-heading"
              fontSize={13}
              fontWeight={700}
            >
              {name.length > 14 ? name.slice(0, 13) + "…" : name}
            </text>
          </g>

          {/* Lane nodes. */}
          {laid.rows.map((lane) => (
            <g key={lane.id}>
              <rect
                x={LANE_X}
                y={lane.cy - 19}
                width={LANE_W}
                height={38}
                rx={8}
                fill="var(--panel-2)"
                stroke="var(--border)"
              />
              <text x={LANE_X + 12} y={lane.cy - 3} fill="var(--foreground)" fontSize={12} fontWeight={600}>
                {lane.label}
              </text>
              {/* The hint. Every lane has carried one since this was written and none of
                  them were ever drawn, which is most of why the graph needed explaining
                  out loud: "Skills" and "Delegate" mean nothing on their own. */}
              <text x={LANE_X + 12} y={lane.cy + 11} fill="var(--muted-foreground)" fontSize={9}>
                {lane.hint}
              </text>
              <text
                x={LANE_X + LANE_W - 12}
                y={lane.cy - 3}
                fill="var(--muted-foreground)"
                fontSize={11}
                textAnchor="end"
              >
                {lane.calls.length}
              </text>
            </g>
          ))}

          {/* Call nodes. */}
          {laid.rows.map((lane) =>
            lane.calls.map((c, i) => {
              const y = lane.y + i * ROW_H;
              const busy = pending(c);
              const isOpen = selected === c.id;
              return (
                <g
                  key={c.id}
                  onClick={() => setSelected(isOpen ? null : c.id)}
                  style={{ cursor: "pointer" }}
                >
                  <rect
                    x={NODE_X}
                    y={y + 3}
                    width={NODE_W}
                    height={ROW_H - 8}
                    rx={7}
                    fill={isOpen ? "var(--brand-soft)" : "var(--panel)"}
                    stroke={busy ? "var(--brand-primary)" : isOpen ? "var(--brand-primary)" : "var(--border)"}
                    strokeDasharray={busy ? "4 3" : undefined}
                  />
                  <circle
                    cx={NODE_X + 14}
                    cy={y + ROW_H / 2 - 1}
                    r={3.5}
                    fill={busy ? "var(--brand-primary)" : "var(--brand-secondary)"}
                    className={busy ? "animate-pulse" : undefined}
                  />
                  <text x={NODE_X + 26} y={y + ROW_H / 2 + 3} fill="var(--foreground)" fontSize={11.5}>
                    {nodeLabel(c)}
                  </text>
                  <text
                    x={NODE_X + NODE_W - 10}
                    y={y + ROW_H / 2 + 3}
                    fill="var(--muted-foreground)"
                    fontSize={10}
                    textAnchor="end"
                  >
                    {resultSize(c)}
                  </text>
                </g>
              );
            }),
          )}
        </svg>

        {/* Subagent lanes, when the server dispatches any. */}
        {subagents.length > 0 && (
          <div className="mt-4 space-y-2">
            {subagents.map((s) => (
              <div key={s.key} className="rounded-lg border border-border bg-panel p-3">
                <div className="flex items-center gap-2">
                  <span className="rounded bg-[var(--brand-soft)] px-2 py-0.5 text-[11px] font-semibold">
                    {s.type || s.label}
                  </span>
                  {!s.done && (
                    <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--brand-primary)]" />
                  )}
                  <span className="truncate text-xs text-muted-foreground">{s.invokedWith}</span>
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {s.chips.map((c) => (
                    <button
                      key={c.id}
                      onClick={() => setSelected(selected === c.id ? null : c.id)}
                      className="rounded border border-border px-2 py-0.5 text-[11px] hover:border-[var(--brand-primary)]"
                    >
                      {c.name}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Inspector for the selected node. */}
      {openChip && (
        <div className="max-h-[38%] flex-shrink-0 overflow-auto border-t border-border bg-panel px-5 py-3">
          <div className="flex items-center gap-2">
            <span className="font-heading text-sm font-semibold">{openChip.name}</span>
            <button
              onClick={() => setSelected(null)}
              className="ml-auto text-xs text-muted-foreground hover:text-foreground"
            >
              close
            </button>
          </div>
          {openChip.code ? (
            <pre className="mt-2 overflow-auto rounded bg-panel-2 p-2 text-[11px] whitespace-pre-wrap">
              {openChip.code}
            </pre>
          ) : (
            openChip.arg && (
              <div className="mt-1 text-xs break-words text-muted-foreground">{openChip.arg}</div>
            )
          )}
          <div className="mt-2 text-[11px] whitespace-pre-wrap break-words">
            {openChip.result === null ? (
              <span className="text-muted-foreground">still running…</span>
            ) : (
              openChip.result.slice(0, 1400)
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default AgentGraph;
