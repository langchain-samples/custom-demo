/**
 * The "+ New" create flow, as a MODAL. Owner is prefilled from the last-used value
 * (localStorage "lastOwner", cached on create by the parent). Customer is required
 * (used as the assistant name). Website + Use case are optional; the setup agent
 * tailors personas / data-gap / tools / prompt from them. The failure mode selects
 * the built-in demo bug (hallucination today). Tools/capabilities are chosen by the
 * setup agent and stay editable afterwards in Settings. Create/Cancel are handled
 * by the parent, which runs the assistant_setup graph then creates + selects the
 * assistant.
 *
 * A modal rather than a panel section, because it used to sit INSIDE the settings
 * sheet directly above the controls that edit the CURRENT assistant: two sets of
 * live fields, one describing a customer that does not exist yet and one editing
 * the one on screen. Everything behind the modal is inert while it is open, so
 * there is only ever one thing to fill in.
 *
 * Mounted only while visible, so each open starts from a fresh prefill.
 */
import { useEffect, useState, type ReactNode } from "react";
import type { Workspace } from "@/lib/api";
import { useSetupCountdown } from "./useSetupCountdown";
import { Combobox } from "@/components/ui/combobox";
import { guessWebsite } from "@/lib/website";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

/** The "?" bubble the field labels use to explain themselves. */
function Hint({ children }: { children: ReactNode }) {
  return (
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
          <span className="block w-[260px] whitespace-normal text-left">
            {children}
          </span>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

/**
 * Preselected when nothing is chosen yet, best match first. Matched by NAME, not id: a
 * hardcoded uuid is right for exactly one organization and silently selects nothing
 * everywhere else, whereas these names either exist or the field stays empty and asks.
 *
 * A list rather than one name because an exact-match on "demo" alone found nothing in an
 * org whose workspaces are called "Default Workspace" and "Demo Workspace", which is how
 * the picker ended up blank.
 */
const DEFAULT_WORKSPACE_NAMES = ["default workspace", "demo workspace", "demo", "default"];
/** Remembers the demo-traffic choice between creates. */
const DEMO_TRAFFIC_LS_KEY = "newAssistantDemoTraffic";

function readFlag(key: string): boolean {
  try {
    return localStorage.getItem(key) === "1";
  } catch {
    return false; // private mode: the safe answer for a switch that costs money
  }
}

function writeFlag(key: string, on: boolean): void {
  try {
    localStorage.setItem(key, on ? "1" : "0");
  } catch {
    /* ignore */
  }
}

export interface NewAssistantValues {
  /**
   * Chosen IN this dialog, and only when the panel has no workspace yet - i.e. a first-run
   * user, who has nothing selected behind the modal. Blank means "use the panel's".
   */
  workspace: string;
  owner: string;
  customer: string;
  website: string;
  useCase: string;
  /** "none" | "hallucination" — the built-in failure mode to demo. */
  failureMode: string;
  /** "prompt_hub" | "context_hub" — where the system prompt is stored. */
  promptSource: string;
  /** Backfill the trace project with synthetic traffic (off unless asked for). */
  demoTraffic: boolean;
}

interface Props {
  initialOwner: string;
  /** Org the workspaces belong to, shown under the picker. Empty hides the mention. */
  organization?: string;
  /**
   * True when a saved workspace was dropped because it no longer exists (an org move).
   * The picker would otherwise appear with no explanation, looking like a bug rather
   * than a question.
   */
  workspaceReset?: boolean;
  /** The panel's current workspace. Empty means the dialog must ask for one. */
  initialWorkspace: string;
  /** Offered only when `initialWorkspace` is empty. */
  workspaces: Workspace[];
  creating: boolean;
  onCreate: (values: NewAssistantValues) => void;
  onCancel: () => void;
}

const IGNORE_AUTOFILL = {
  autoComplete: "off",
  "data-1p-ignore": "true",
  "data-lpignore": "true",
} as const;

export function NewAssistantDialog({
  initialOwner,
  initialWorkspace,
  workspaces,
  organization = "",
  workspaceReset = false,
  creating,
  onCreate,
  onCancel,
}: Props) {
  // Hidden once we know who you are. It is prefilled from localStorage and almost never
  // changed, so on every run after the first it was a field to skip past. Change it in
  // Customize if you need to.
  const knowsOwner = !!initialOwner;
  const [owner, setOwner] = useState(initialOwner);
  // Asked for only when there is nothing selected behind the modal. On a first run this is
  // the whole reason the separate onboarding dialog used to exist; the rest of it was this
  // same form with fewer fields.
  const needsWorkspace = !initialWorkspace;
  const [workspace, setWorkspace] = useState("");

  // Default to "Demo" once the list arrives. An effect and not a useState initializer,
  // because the workspaces load asynchronously and are usually still empty at mount - the
  // initializer would run once against nothing and never fire again. Guarded on `workspace`
  // so it only ever fills a BLANK field and cannot overwrite a deliberate choice.
  useEffect(() => {
    if (!needsWorkspace || workspace) return;
    const named = (want: string) =>
      workspaces.find((w) => (w.name || "").trim().toLowerCase() === want);
    const fallback = DEFAULT_WORKSPACE_NAMES.map(named).find(Boolean);
    if (fallback) setWorkspace(fallback.id);
  }, [needsWorkspace, workspace, workspaces]);
  const [customer, setCustomer] = useState("");
  const [website, setWebsite] = useState("");
  // Once you type in the website field it is yours: the guess below stops overwriting it,
  // including when you clear it, which is how you say "this customer has no site".
  const [websiteTouched, setWebsiteTouched] = useState(false);
  const [useCase, setUseCase] = useState("");
  const [failureMode, setFailureMode] = useState("hallucination");
  const [promptSource, setPromptSource] = useState("context_hub");
  /**
   * Sticky across creates. Still OFF by default on a machine that has never set it - it
   * ingests thousands of priced runs into the customer's project, and nobody should get
   * that by accident - but a presenter who wants it wants it every time, and re-ticking it
   * on every demo was the only reason to open this row at all.
   */
  const [demoTraffic, setDemoTraffic] = useState(() => readFlag(DEMO_TRAFFIC_LS_KEY));

  const secondsLeft = useSetupCountdown(creating);

  const canCreate = !!customer.trim() && (!needsWorkspace || !!workspace) && !creating;

  return (
    <Dialog open onOpenChange={(o) => !o && onCancel()}>
      <DialogContent
        className="sm:max-w-lg"
        showCloseButton={!creating}
        // Setup is a minute of real work (branding, prompts, skills, a dataset and a
        // baseline experiment). A stray Esc or click-away mid-run would leave all of
        // that half-built with nothing on screen saying so.
        onEscapeKeyDown={(e) => creating && e.preventDefault()}
        onInteractOutside={(e) => creating && e.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle>New customer demo</DialogTitle>
          <DialogDescription>
            Builds an assistant from the customer's brand, with quick actions,
            skills and its own trace project.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-2">
          {needsWorkspace && (
            <div className="flex flex-col gap-1.5">
              {workspaceReset && (
                <p className="m-0 rounded-md border border-border bg-panel-2 px-2 py-1.5 text-[11px] leading-snug text-foreground">
                  Your saved workspace is not in{" "}
                  {organization ? organization : "this organization"} any more, so it has
                  been cleared. Pick one to carry on.
                </p>
              )}
              <Combobox
                options={workspaces.map((w) => ({ value: w.id, label: w.name || w.id }))}
                value={workspace}
                onChange={setWorkspace}
                placeholder="Choose a workspace to log to…"
                searchPlaceholder="Filter workspaces…"
                emptyText="No workspaces."
              />
              <p className="m-0 text-[11px] leading-snug text-muted-foreground">
                {organization ? `Workspaces in ${organization}. ` : ""}
                Traces, prompts and datasets all land here. You can change it later in
                Customize.
              </p>
            </div>
          )}
          {!knowsOwner && (
            <Input
              placeholder="Your name"
              value={owner}
              onChange={(e) => setOwner(e.target.value)}
              {...IGNORE_AUTOFILL}
            />
          )}
          <Input
            placeholder="Customer (used as the assistant name)"
            value={customer}
            onChange={(e) => {
              setCustomer(e.target.value);
              if (!websiteTouched) setWebsite(guessWebsite(e.target.value));
            }}
            {...IGNORE_AUTOFILL}
          />
          <Input
            placeholder="Website (optional, e.g. acme.com)"
            value={website}
            onChange={(e) => {
              setWebsiteTouched(true);
              setWebsite(e.target.value);
            }}
            {...IGNORE_AUTOFILL}
          />
          <Textarea
            placeholder="Use case (optional; defaults to an internal assistant). e.g. support ops reviewing ticket volume & CSAT, drafting follow-ups"
            value={useCase}
            onChange={(e) => setUseCase(e.target.value)}
            rows={1}
            className="min-h-0 resize-none text-[13px]"
            {...IGNORE_AUTOFILL}
          />

          <div className="flex items-center gap-2">
            <Label className="flex-row items-center gap-1.5 text-[12.5px] font-medium text-foreground">
              Failure mode
              <Hint>
                <strong>Hallucination</strong> seeds a built-in demo bug: the
                synthetic data source withholds one customer-specific metric,
                and the last quick-action question probes it, so after two
                grounded answers the agent visibly fabricates over the missing
                data. Great for showing how tracing/evals catch it.{" "}
                <strong>None</strong> = a clean, grounded assistant.
              </Hint>
            </Label>
            <Select value={failureMode} onValueChange={setFailureMode}>
              <SelectTrigger className="h-8 flex-1 text-[13px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">None (clean)</SelectItem>
                <SelectItem value="hallucination">Hallucination</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="flex items-center gap-2">
            <Label className="flex-row items-center gap-1.5 text-[12.5px] font-medium text-foreground">
              Prompt source
            </Label>
            <Select value={promptSource} onValueChange={setPromptSource}>
              <SelectTrigger className="h-8 flex-1 text-[13px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="context_hub">
                  Context Hub (AGENTS.md)
                </SelectItem>
                <SelectItem value="prompt_hub">Prompt Hub</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <label className="flex cursor-pointer items-center gap-2">
            <Switch
              checked={demoTraffic}
              onCheckedChange={(on) => {
                setDemoTraffic(on);
                writeFlag(DEMO_TRAFFIC_LS_KEY, on);
              }}
            />
            <span className="flex items-center gap-1.5 text-[12.5px] font-medium text-foreground">
              Backfill demo traffic
              <Hint>
                Ingests roughly a day of <strong>synthetic</strong> traffic (a
                few thousand backdated runs) into this customer's LangSmith
                project. This way, Monitoring, Insights and Engine have
                something to show. Also it queues 10 traces in the AQ. These
                traces weren't actually run so the cost LangSmith estimates for
                it (often a few hundred dollars) is not a real charge. You can
                also generate it later from Settings.
              </Hint>
            </span>
          </label>

        </div>

        <DialogFooter>
          <Button
            type="button"
            size="sm"
            className="flex-1"
            disabled={!canCreate}
            onClick={() =>
              onCreate({
                workspace,
                owner: owner.trim(),
                customer: customer.trim(),
                website: website.trim(),
                useCase: useCase.trim(),
                failureMode,
                promptSource,
                demoTraffic,
              })
            }
          >
            {creating
              ? secondsLeft === null
                ? "Setting up…"
                : `Setting up… ${secondsLeft.toFixed(1)}`
              : "Create"}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="flex-1 text-primary"
            disabled={creating}
            onClick={onCancel}
          >
            Cancel
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
