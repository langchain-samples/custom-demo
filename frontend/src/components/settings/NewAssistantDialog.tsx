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
import { useEffect, useRef, useState, type ReactNode } from "react";
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

export interface NewAssistantValues {
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
  creating: boolean;
  onCreate: (values: NewAssistantValues) => void;
  onCancel: () => void;
}

const IGNORE_AUTOFILL = {
  autoComplete: "off",
  "data-1p-ignore": "true",
  "data-lpignore": "true",
} as const;

/** Setup's typical wall clock. Not a deadline - see useSetupCountdown. */
const SETUP_SECONDS = 60;

/**
 * Seconds remaining of the expected setup time, to one decimal, or null once it has run
 * out. A minute of a blank progressless button is long enough that people assume it has
 * hung and click away, and setup is genuinely a minute of real work.
 *
 * It counts down an ESTIMATE, not a deadline: nothing here can know when the LLM will
 * finish. So it stops at zero and drops back to a plain "Setting up…" rather than showing
 * a negative number or freezing at 0.0 - both of which read as broken, which is the exact
 * impression the countdown exists to prevent.
 *
 * 100ms so the tenths actually move. Interval rather than an animation frame: this is one
 * short text node, and rAF would repaint it 60 times a second to show the same digit.
 */
function useSetupCountdown(running: boolean): number | null {
  const [left, setLeft] = useState<number | null>(null);
  const startedAt = useRef(0);

  useEffect(() => {
    if (!running) {
      setLeft(null);
      return;
    }
    startedAt.current = Date.now();
    setLeft(SETUP_SECONDS);
    const id = setInterval(() => {
      const remaining = SETUP_SECONDS - (Date.now() - startedAt.current) / 1000;
      setLeft(remaining > 0 ? remaining : null);
    }, 100);
    return () => clearInterval(id);
  }, [running]);

  return left;
}

export function NewAssistantDialog({
  initialOwner,
  creating,
  onCreate,
  onCancel,
}: Props) {
  const [owner, setOwner] = useState(initialOwner);
  const [customer, setCustomer] = useState("");
  const [website, setWebsite] = useState("");
  const [useCase, setUseCase] = useState("");
  const [failureMode, setFailureMode] = useState("hallucination");
  const [promptSource, setPromptSource] = useState("context_hub");
  const [demoTraffic, setDemoTraffic] = useState(false);

  const secondsLeft = useSetupCountdown(creating);

  const canCreate = !!customer.trim() && !creating;

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
          <Input
            placeholder="Owner name"
            value={owner}
            onChange={(e) => setOwner(e.target.value)}
            {...IGNORE_AUTOFILL}
          />
          <Input
            placeholder="Customer (used as the assistant name)"
            value={customer}
            onChange={(e) => setCustomer(e.target.value)}
            {...IGNORE_AUTOFILL}
          />
          <Input
            placeholder="Website (optional, e.g. acme.com)"
            value={website}
            onChange={(e) => setWebsite(e.target.value)}
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
            <Switch checked={demoTraffic} onCheckedChange={setDemoTraffic} />
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
