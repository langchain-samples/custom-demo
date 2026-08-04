/**
 * "Generate demo traffic" — backfills the assistant's LangSmith trace project with
 * a day of synthetic traffic so the Monitoring and Insights tabs have something to
 * show during a demo.
 *
 * New assistants get this automatically at setup; this button exists for the ones
 * created before the feature (and for topping a project back up after a cleanup).
 *
 * It is a plain action, not a toggle: the backfill is one batch of backdated runs,
 * so there is no "on" state to represent. The only transient state is "running",
 * which we poll for because the job takes minutes (several real agent turns for
 * seed traces, then a few thousand run ingests).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  generateDemoTraffic,
  getDemoTrafficStatus,
  type DemoTrafficStatus,
  type DemoTrafficTarget,
} from "@/lib/api";

const POLL_MS = 5000;

interface Props {
  target: DemoTrafficTarget | null;
}

export function DemoTraffic({ target }: Props) {
  const [status, setStatus] = useState<DemoTrafficStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");
  const alive = useRef(true);

  const project = target?.project || "";
  const busy = starting || Boolean(status?.running);

  const refresh = useCallback(async () => {
    if (!project) return;
    const next = await getDemoTrafficStatus(project, target?.workspace);
    if (alive.current) setStatus(next);
  }, [project, target?.workspace]);

  useEffect(() => {
    // Re-armed in the effect body rather than only in cleanup, because React 19
    // StrictMode mounts, unmounts and remounts — a ref left false would freeze
    // the panel on its first render.
    alive.current = true;
    void refresh();
    if (!busy) return () => { alive.current = false; };
    const id = setInterval(() => void refresh(), POLL_MS);
    return () => {
      alive.current = false;
      clearInterval(id);
    };
  }, [refresh, busy]);

  const start = useCallback(async () => {
    if (!target) return;
    setStarting(true);
    setError("");
    const ack = await generateDemoTraffic(target);
    if (!ack.ok) setError(ack.error || "could not start");
    // Let the poll take over; the server owns "running" from here.
    setStarting(false);
    void refresh();
  }, [target, refresh]);

  if (!target) return null;

  const result = status?.result;
  return (
    <div className="mt-2 border-t border-border pt-3">
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium">Demo traffic</div>
          <div className="truncate text-xs text-muted-foreground">
            {busy
              ? "Generating… seed runs then backfill, a few minutes."
              : result?.traces
                ? `${result.traces} traces over the last ${result.hours ?? 23}h` +
                  (result.gap_traces ? ` · ${result.gap_traces} showing the failure mode` : "")
                : `Backfill a day of traffic into "${project}" for Monitoring & Insights.`}
          </div>
        </div>
        <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void start()}>
          {busy ? "Generating…" : "Generate"}
        </Button>
      </div>
      {(error || result?.error) && (
        <div className="mt-2 text-xs text-destructive">{error || result?.error}</div>
      )}
      {result?.insights?.job_error && (
        <div className="mt-2 text-xs text-muted-foreground">{result.insights.job_error}</div>
      )}
    </div>
  );
}
