/**
 * Inline "+ New" create form (section 2). Owner is prefilled from the last-used
 * value (localStorage "lastOwner", cached on create by the parent). Customer is
 * required (used as the assistant name). Website + Use case are optional; the
 * setup agent tailors personas / data-gap / tools / prompt from them. The failure
 * mode selects the built-in demo bug (hallucination today). Tools/capabilities are
 * chosen by the setup agent and stay editable afterwards in Settings.
 * Create/Cancel are handled by the parent, which runs the assistant_setup graph
 * then creates + selects the assistant.
 *
 * Mounted only while visible, so each open starts from a fresh prefill.
 */
import { useState } from "react";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
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

export interface NewAssistantValues {
  owner: string;
  customer: string;
  website: string;
  useCase: string;
  /** "none" | "hallucination" — the built-in failure mode to demo. */
  failureMode: string;
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

export function NewAssistantForm({ initialOwner, creating, onCreate, onCancel }: Props) {
  const [owner, setOwner] = useState(initialOwner);
  const [customer, setCustomer] = useState("");
  const [website, setWebsite] = useState("");
  const [useCase, setUseCase] = useState("");
  const [failureMode, setFailureMode] = useState("hallucination");

  const canCreate = !!customer.trim() && !creating;

  return (
    <div className="flex flex-col gap-2 rounded-xl border border-border p-2.5">
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
                  <strong>Hallucination</strong> seeds a built-in demo bug: the synthetic
                  data source withholds one customer-specific metric, and the last
                  quick-action question probes it, so after two grounded answers the agent
                  visibly fabricates over the missing data. Great for showing how
                  tracing/evals catch it. <strong>None</strong> = a clean, grounded assistant.
                </span>
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
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

      <div className="mt-0.5 flex gap-2">
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
            })
          }
        >
          {creating ? "Setting up…" : "Create"}
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
      </div>
    </div>
  );
}
