/**
 * Inline "+ New" create form (section 2). Owner is prefilled from the last-used
 * value (localStorage "lastOwner", cached on create by the parent). Customer is
 * required (used as the assistant name). Website is optional. The hallucination
 * toggle seeds the built-in demo bug. Create/Cancel are handled by the parent,
 * which runs the assistant_setup graph then creates + selects the assistant.
 *
 * Mounted only while visible, so each open starts from a fresh prefill.
 */
import { useState } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
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
  hallucination: boolean;
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
  const [hallucination, setHallucination] = useState(true);

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
      <Label className="flex-row items-center gap-2 text-[12.5px] font-medium text-foreground">
        <Switch
          checked={hallucination}
          onCheckedChange={(v) => setHallucination(!!v)}
        />
        Build in hallucination demo
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
            <TooltipContent className="max-w-[240px] text-xs leading-relaxed">
              Seeds a built-in demo bug: the synthetic data source withholds one
              customer-specific metric, and the <strong>last</strong> quick-action
              question probes it — so after two grounded answers the agent visibly
              fabricates over the missing data. Great for showing how tracing/evals
              catch hallucinations. Leave off for a clean assistant.
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      </Label>
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
              hallucination,
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
