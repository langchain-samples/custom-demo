/**
 * Body of the evals dialog: the assistant's LangSmith dataset, the score of its
 * latest experiment, and the button that re-runs it.
 *
 * This is the mid-demo beat. The baseline experiment fails the third example
 * (the data-gap probe the buggy prompt fabricates over), so the badge reads a
 * loud red "2/3 passing". The presenter fixes the prompt in Prompt Hub, clicks
 * Run experiment, and the badge turns green at 3/3 — the agent pulls its prompt
 * fresh from the Hub on every question, so no redeploy sits in between.
 *
 * POLARITY, because it is the one thing that would silently ruin the demo:
 * score 1 means CORRECT behaviour (the answer admitted the data was missing).
 * `passed` is therefore "examples the agent got right", and `passed === total`
 * is the green state. This is the opposite of the repo-level evals in `evals/`,
 * which score 1 when the planted bug fires.
 *
 * All LangSmith state is read back from LangSmith itself (there is no
 * server-side run store), so closing the dialog or reloading the page mid-run
 * loses nothing.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  IconAlertTriangle,
  IconCircleCheck,
  IconExternalLink,
  IconFlaskOff,
  IconLoader2,
  IconPlayerPlay,
  IconRefresh,
} from "@tabler/icons-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PaneState } from "@/components/files/PaneState";
import {
  runEvalExperiment,
  type EvalTarget,
} from "@/lib/api";
import { useDemoTrafficStatus, useEvalStatus } from "@/lib/queries";

/**
 * Hard stop for the optimistic "pending" state. POST /evals/run answers as soon
 * as it has spawned the run, so for the first seconds LangSmith has nothing to
 * report and `running` is still false — we hold the spinner ourselves until a
 * NEW experiment name lands. This cap means a run that dies inside that daemon
 * thread can't leave the panel spinning forever.
 */
const PENDING_TIMEOUT_MS = 8 * 60_000;

/**
 * The score, as loud as the demo needs it: solid red below full marks, solid
 * green at full marks. Deliberately a filled badge rather than the tinted
 * `destructive` idiom used elsewhere — this has to read across a room, in both
 * themes, from the back of the audience.
 */
function ScoreBadge({ passed, total }: { passed: number; total: number }) {
  const green = total > 0 && passed >= total;
  return (
    <Badge
      className={
        "h-6 gap-1 px-2.5 text-[13px] font-semibold text-white " +
        (green ? "bg-success" : "bg-danger")
      }
    >
      {green ? <IconCircleCheck /> : <IconAlertTriangle />}
      {passed}/{total} passing
    </Badge>
  );
}

/**
 * The calm empty state. Reached two ways — the assistant's metadata never named
 * a dataset (checked before any request, in EvalPanel) or LangSmith no longer
 * has one — and both are ordinary, so the copy lives here once and neither
 * caller is allowed to phrase it as a failure.
 */
export function NoEvalDataset() {
  return (
    <PaneState
      icon={<IconFlaskOff size={26} />}
      title="No eval dataset for this assistant"
      detail="Setup provisions one alongside the assistant. Create a new assistant to get a dataset built from its quick actions."
    />
  );
}

/** One `LABEL / value` row — the panel is three of these stacked. */
function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">
        {label}
      </span>
      {children}
    </div>
  );
}

/** External LangSmith link, rendered only when the server resolved a URL. */
function OpenLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="inline-flex items-center gap-1 text-[13px] text-primary hover:underline"
    >
      {children}
      <IconExternalLink size={13} />
    </a>
  );
}

export function EvalRunner({ target }: { target: EvalTarget }) {
  const [starting, setStarting] = useState(false);
  const [pendingSince, setPendingSince] = useState<number | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  // Polled only while something is in flight. `pollWanted` is computed from the state
  // above, before the query, so the query can consume it without a circular read.
  const pollWanted = starting || pendingSince !== null;
  const statusQuery = useEvalStatus(target, pollWanted);

  // The experiment showing when the current run was kicked off; a *different*
  // name coming back is how we know the new one has landed. A ref, so the poll
  // callback can read it without being rebuilt (and restarting the interval).
  const priorExperiment = useRef<string | null>(null);

  // The query owns the interval now, so the setInterval and the `alive` ref that used to
  // guard setState after the dialog closed are both gone: react-query drops the result
  // of a query whose observer has unmounted, which is what the ref was for (and what
  // StrictMode's double mount made visible). `busy` is now only about the UI.
  const busy = starting || !!statusQuery.data?.running || pendingSince !== null;
  const status = statusQuery.data ?? null;
  const refresh = useCallback(() => void statusQuery.refetch(), [statusQuery]);

  // Clearing the optimistic pending state still needs to watch each new status:
  // POST /evals/run answers before LangSmith has anything to report, so a NEW
  // experiment name landing is the signal that the real run has started.
  useEffect(() => {
    if (!status) return;
    setPendingSince((since) => {
      if (since === null) return since;
      const landed =
        !status.running &&
        !!status.experiment_name &&
        status.experiment_name !== priorExperiment.current;
      return landed || Date.now() - since > PENDING_TIMEOUT_MS ? null : since;
    });
  }, [status]);

  const handleRun = useCallback(async () => {
    setRunError(null);
    setStarting(true);
    priorExperiment.current = status?.experiment_name ?? null;
    // runEvalExperiment resolves with { ok: false } rather than throwing.
    const ack = await runEvalExperiment(target);
    setStarting(false);
    if (!ack.ok) {
      setRunError(ack.error || "Could not start the experiment.");
      return;
    }
    setPendingSince(Date.now());
    void refresh();
  }, [refresh, status?.experiment_name, target]);

  if (status === null) {
    return (
      <PaneState icon={<IconLoader2 size={26} className="animate-spin" />} title="Checking evals…" />
    );
  }

  // A lookup that genuinely broke (network, 500) — distinct from "no dataset",
  // which is an ordinary answer and gets the calm empty state below.
  if (status.error && !status.dataset_name) {
    return (
      <PaneState
        icon={<IconAlertTriangle size={26} />}
        title="Couldn't reach LangSmith"
        detail={status.error}
        action={
          <Button variant="secondary" size="sm" className="mt-1" onClick={() => void refresh()}>
            <IconRefresh size={14} /> Try again
          </Button>
        }
      />
    );
  }

  if (!status.dataset_name) return <NoEvalDataset />;

  // `total: 0` means LangSmith has no scored runs to report yet, not a 0/0
  // score — it reads as a red badge otherwise, which would be a lie.
  const total = status.total ?? 0;
  const hasScore = typeof status.passed === "number" && total > 0;

  return (
    <div className="flex flex-col gap-3.5">
      <Row label="Dataset">
        {status.dataset_url ? (
          <OpenLink href={status.dataset_url}>
            <span className="font-mono text-[12px]">{status.dataset_name}</span>
          </OpenLink>
        ) : (
          <span className="font-mono text-[12px]">{status.dataset_name}</span>
        )}
      </Row>

      <Row label="Latest experiment">
        {busy ? (
          <span className="inline-flex items-center gap-1.5 text-[13px] text-muted-foreground">
            <IconLoader2 size={14} className="animate-spin" />
            Running - three real agent turns, usually 30-90s.
          </span>
        ) : hasScore ? (
          <div className="flex flex-wrap items-center gap-2">
            <ScoreBadge passed={status.passed ?? 0} total={total} />
            {status.url && status.experiment_name ? (
              <OpenLink href={status.url}>{status.experiment_name}</OpenLink>
            ) : (
              <span className="text-[13px] text-muted-foreground">{status.experiment_name}</span>
            )}
          </div>
        ) : (
          <span className="text-[13px] text-muted-foreground">
            No experiment has run against this dataset yet.
          </span>
        )}
      </Row>

      <div className="flex items-center gap-2">
        <Button size="sm" disabled={busy} onClick={() => void handleRun()}>
          {busy ? <IconLoader2 className="animate-spin" /> : <IconPlayerPlay />}
          {busy ? "Running…" : "Run experiment"}
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          title="Re-read the latest experiment from LangSmith"
          aria-label="Refresh eval status"
          onClick={() => void refresh()}
        >
          <IconRefresh size={15} />
        </Button>
      </div>

      {/*
        Three different failures, one line, in the order the presenter can act on:
        the click itself failed, the lookup failed, or — the quiet one this exists
        for — the detached run died. That last one leaves the previous score on
        screen looking untouched, so saying it out loud is the difference between
        "my prompt fix did nothing" and "the run never started".
      */}
      {runError || status.error || status.last_error ? (
        <p className="m-0 flex items-start gap-1.5 text-[12px] text-destructive">
          <IconAlertTriangle size={13} className="mt-px shrink-0" />
          {runError || status.error || `Last run failed before it finished: ${status.last_error}`}
        </p>
      ) : null}

      <DemoResources project={target.project || ""} workspace={target.workspace} />
    </div>
  );
}

/**
 * The rest of the demo's LangSmith surface: how much synthetic traffic is in the
 * trace project, and jump-off links to the places a presenter actually navigates
 * to — the project, its Insights tab and its Engine tab.
 *
 * Separate from the eval state above because it comes from a different endpoint
 * and a different lifecycle: the traffic is backfilled once at setup, whereas the
 * experiment is re-run mid-demo. Links are absent until the project exists, which
 * is the normal state before any traffic has been generated.
 */
function DemoResources({ project, workspace }: { project: string; workspace?: string }) {
  // A one-shot read, so `poll` is false: this panel is usually opened long after the
  // backfill ran. It shares a cache entry with the DemoTraffic control in the settings
  // panel, so opening both no longer means two requests for the same thing.
  const traffic = useDemoTrafficStatus(project, workspace, false).data ?? null;

  if (!project) return null;
  const links = traffic?.links || {};
  const result = traffic?.result;
  // Durable count from LangSmith; the receipt above is richer but does not survive a
  // redeploy, and this panel is often opened long after the backfill ran.
  const seeded = traffic?.traffic?.traces;
  const hasLinks = links.project || links.insights || links.engine;

  return (
    <div className="flex flex-col gap-3 border-t border-border pt-3">
      <Row label="Demo traffic">
        {traffic?.running ? (
          <span className="inline-flex items-center gap-1.5 text-[13px] text-muted-foreground">
            <IconLoader2 size={13} className="animate-spin" />
            Generating: seed runs, then backfill.
          </span>
        ) : result?.traces ? (
          <span className="text-[13px] text-muted-foreground">
            {result.traces} traces over the last {result.hours ?? 23}h
            {result.gap_traces ? ` · ${result.gap_traces} fabricating over the data gap` : ""}
          </span>
        ) : seeded ? (
          /* No receipt in this process (a redeploy, or another replica ran it), but
             LangSmith can still count the tagged runs — which beats telling the
             presenter there is no traffic while they are looking at it. */
          <span className="text-[13px] text-muted-foreground">
            {seeded} synthetic traces in the project
          </span>
        ) : (
          <span className="text-[13px] text-muted-foreground">No demo traffic yet.</span>
        )}
      </Row>

      {hasLinks ? (
        <Row label="Open in LangSmith">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            {links.project && <OpenLink href={links.project}>Project</OpenLink>}
            {links.insights && <OpenLink href={links.insights}>Insights</OpenLink>}
            {links.engine && <OpenLink href={links.engine}>Engine</OpenLink>}
          </div>
        </Row>
      ) : null}
    </div>
  );
}
