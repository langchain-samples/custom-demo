/**
 * "Evals" — a small toolbar dialog over the LangSmith dataset that setup
 * provisioned for the active assistant:
 *
 *   ┌───────────── Evals ─────────────────── ✕ ┐
 *   │  DATASET             corebot-acme-evals  │
 *   │  LATEST EXPERIMENT   [ 2/3 passing ] ↗   │
 *   │  [ ▶ Run experiment ]  ⟳                 │
 *   └──────────────────────────────────────────┘
 *
 * Chrome only; EvalRunner owns the data and the polling. Radix unmounts dialog
 * children on close, so every open starts from a fresh status read and the poll
 * dies with the dialog — the same arrangement FileBrowser/SandboxBrowser uses.
 *
 * An assistant with no dataset is NORMAL, not broken: every assistant created
 * before this feature is in that state, and dataset creation during setup is
 * best-effort. We can tell from `metadata.ls_artifacts.eval_dataset` alone, so
 * that case renders a calm empty state without a request.
 */
import { useMemo } from "react";
import { IconUserOff } from "@tabler/icons-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { PaneState } from "@/components/files/PaneState";
import { EvalRunner, NoEvalDataset } from "@/components/evals/EvalRunner";
import type { Assistant, EvalTarget } from "@/lib/api";

interface EvalPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /**
   * The active assistant. Its metadata names the dataset and its context names
   * the workspace that dataset lives in — the object App already holds, not a
   * second fetch.
   */
  assistant: Assistant | null;
}

export function EvalPanel({ open, onOpenChange, assistant }: EvalPanelProps) {
  const assistantId = assistant?.assistant_id ?? null;
  const dataset = assistant?.metadata?.ls_artifacts?.eval_dataset;
  const workspace = assistant?.context?.ls_workspace;
  // The whole stored context, not just the workspace: the server rebuilds the
  // runtime Context from it so the experiment runs THIS assistant's prompt
  // handle and planted gap. Depend on the object identity — it comes from the
  // assistant App holds, so it only changes when the assistant does.
  const context = assistant?.context;

  const target = useMemo<EvalTarget | null>(
    () =>
      assistantId
        ? {
            assistant_id: assistantId,
            dataset: dataset || undefined,
            workspace: workspace || undefined,
            context,
          }
        : null,
    [assistantId, dataset, workspace, context],
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md print:hidden">
        <DialogHeader>
          <DialogTitle>Evals</DialogTitle>
          <DialogDescription>
            Run this assistant's demo questions as a LangSmith experiment. A pass means the answer
            stayed grounded - including admitting when the data isn't there.
          </DialogDescription>
        </DialogHeader>

        {!target ? (
          <PaneState
            icon={<IconUserOff size={26} />}
            title="No assistant selected"
            detail="Pick or create an assistant in Settings - evals run against that assistant's dataset."
          />
        ) : !dataset ? (
          <NoEvalDataset />
        ) : (
          <EvalRunner target={target} />
        )}
      </DialogContent>
    </Dialog>
  );
}
