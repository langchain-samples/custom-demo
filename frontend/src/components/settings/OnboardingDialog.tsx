/**
 * First-run onboarding for a new Demo Engineer. Shown once (when no owner name
 * is cached in localStorage): capture their name + a workspace to log to, then
 * spin up their first demo. Reuses the same setup flow as the "+ New" form.
 */
import { useState } from "react";
import type { Workspace } from "@/lib/api";
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

const INTERNAL_USE_CASE =
  "An internal assistant for employees to explore company metrics, operations, and performance.";

export function OnboardingDialog({
  open,
  workspaces,
  creating,
  onCreate,
}: {
  open: boolean;
  workspaces: Workspace[];
  creating: boolean;
  onCreate: (name: string, workspace: string, customer: string, useCase: string) => void;
}) {
  const [name, setName] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [customer, setCustomer] = useState("");
  const [useCase, setUseCase] = useState("");

  const canCreate = !!name.trim() && !!workspace && !!customer.trim() && !creating;

  return (
    // Non-dismissible: onboarding completes by creating the first demo.
    <Dialog open={open}>
      <DialogContent showCloseButton={false} className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Welcome — let's get you set up</DialogTitle>
          <DialogDescription>Looks like you're new here. Takes about a minute.</DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-1.5">
          <Label className="text-[12.5px] font-medium">What's your name?</Label>
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Josiah Coad"
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
                placeholder="Optional — defaults to an internal assistant"
                autoComplete="off"
              />
            </div>
          </div>
        </div>

        <Button
          className="mt-1 w-full"
          disabled={!canCreate}
          onClick={() => onCreate(name.trim(), workspace, customer.trim(), useCase.trim())}
        >
          {creating ? "Setting up your demo…" : "Create"}
        </Button>
      </DialogContent>
    </Dialog>
  );
}
