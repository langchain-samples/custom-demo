/**
 * First-run onboarding for a new Demo Engineer. Shown once (when no owner name
 * is cached in localStorage): capture their name + a workspace to log to, then
 * spin up their first demo. Reuses the same setup flow as the "+ New" form.
 */
import { useEffect, useState } from "react";
import type { FailureMode, Workspace } from "@/lib/api";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Combobox } from "@/components/ui/combobox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

const INTERNAL_USE_CASE =
  "An internal assistant for employees to explore company metrics, operations, and performance.";

// Preferred default workspace for a new DE (matched by name, case-insensitive).
const DEFAULT_WORKSPACE_NAME = "agent-demo-workspace";

export function OnboardingDialog({
  open,
  workspaces,
  creating,
  onCreate,
}: {
  open: boolean;
  workspaces: Workspace[];
  creating: boolean;
  onCreate: (
    name: string,
    workspace: string,
    customer: string,
    useCase: string,
    failureMode: FailureMode,
  ) => void;
}) {
  const [name, setName] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [customer, setCustomer] = useState("");
  const [useCase, setUseCase] = useState("");
  const [failureMode, setFailureMode] = useState<FailureMode>("hallucination");

  // Once workspaces load, preselect agent-demo-workspace if the DE hasn't picked one.
  useEffect(() => {
    if (workspace) return;
    const match = workspaces.find(
      (w) => (w.name || "").toLowerCase() === DEFAULT_WORKSPACE_NAME,
    );
    if (match) setWorkspace(match.id);
  }, [workspaces, workspace]);

  const canCreate = !!name.trim() && !!workspace && !!customer.trim() && !creating;

  return (
    // Non-dismissible: onboarding completes by creating the first demo.
    <Dialog open={open}>
      <DialogContent showCloseButton={false} className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Welcome! Let's get you set up</DialogTitle>
          <DialogDescription>Looks like you're new here. Takes about a minute.</DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-1.5">
          <Label className="text-[12.5px] font-medium">What's your name?</Label>
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoComplete="off"
          />
          <p className="m-0 text-[11px] leading-snug text-muted-foreground">
            Used to assign you as the owner of any assistants you create.
          </p>
        </div>

        <div className="flex flex-col gap-1.5">
          <Label className="text-[12.5px] font-medium">Workspace</Label>
          <Combobox
            options={workspaces.map((w) => ({ value: w.id, label: w.name || w.id }))}
            value={workspace}
            onChange={setWorkspace}
            placeholder="Choose a workspace to log to…"
            searchPlaceholder="Filter workspaces…"
            emptyText="No workspaces."
          />
          <p className="m-0 text-[11px] leading-snug text-muted-foreground">
            These assistants all log to the <strong>Enterprise Readiness Demos</strong> org. Pick
            the workspace you want traces + prompts to land in.
          </p>
        </div>

        <div className="mt-1 border-t border-border pt-3">
          <p className="m-0 mb-2 text-[12.5px] font-semibold">Now let's set up your first demo</p>

          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5">
              <div className="flex items-center justify-between">
                <Label className="text-[12.5px] font-medium">Customer</Label>
                <button
                  type="button"
                  onClick={() => setCustomer("Walmart")}
                  className="text-[11px] text-[color:var(--brand-label)] underline underline-offset-2 hover:opacity-80"
                >
                  use Walmart
                </button>
              </div>
              <Input
                value={customer}
                onChange={(e) => setCustomer(e.target.value)}
                placeholder="e.g. Walmart"
                autoComplete="off"
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <div className="flex items-center justify-between">
                <Label className="text-[12.5px] font-medium">Use case</Label>
                <button
                  type="button"
                  onClick={() => setUseCase(INTERNAL_USE_CASE)}
                  className="text-[11px] text-[color:var(--brand-label)] underline underline-offset-2 hover:opacity-80"
                >
                  use Internal
                </button>
              </div>
              <Input
                value={useCase}
                onChange={(e) => setUseCase(e.target.value)}
                placeholder="Optional; defaults to an internal assistant"
                autoComplete="off"
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <Label className="flex-row items-center gap-1.5 text-[12.5px] font-medium">
                Failure mode
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span
                        tabIndex={0}
                        className="flex h-4 w-4 cursor-help items-center justify-center rounded-full border border-muted-foreground/50 text-[10px] font-bold text-muted-foreground"
                      >
                        ?
                      </span>
                    </TooltipTrigger>
                    <TooltipContent className="text-xs leading-relaxed">
                      <span className="block w-[280px] whitespace-normal text-left">
                        Each mode plants one built-in demo bug, probed by the last quick action
                        after two grounded answers.
                        <br />
                        <br />
                        <strong>Hallucination</strong>: fabricates a confident answer over data
                        the source withholds.
                        <br />
                        <strong>PII leakage</strong>: discloses a customer's contact info to
                        whoever asks, unverified.
                        <br />
                        <strong>Prompt injection</strong>: caves to a sentimental user ask (e.g. a
                        catchphrase "for grandma"), abandoning its assigned voice to keep them
                        happy.
                        <br />
                        <strong>None</strong> = a clean, grounded assistant.
                      </span>
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              </Label>
              <Select
                value={failureMode}
                onValueChange={(v) => setFailureMode(v as FailureMode)}
              >
                <SelectTrigger className="h-8 text-[13px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">None (clean)</SelectItem>
                  <SelectItem value="hallucination">Hallucination</SelectItem>
                  <SelectItem value="pii_leakage">PII leakage</SelectItem>
                  <SelectItem value="prompt_injection">Prompt injection</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
        </div>

        <Button
          className="mt-1 w-full"
          disabled={!canCreate}
          onClick={() =>
            onCreate(name.trim(), workspace, customer.trim(), useCase.trim(), failureMode)
          }
        >
          {creating ? "Setting up your demo…" : "Create"}
        </Button>
      </DialogContent>
    </Dialog>
  );
}
