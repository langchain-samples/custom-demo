/**
 * Assistant picker (section 2). Options are labeled by assistant name only;
 * placeholder "Select an assistant…" with no graph-default option. A "+ New"
 * button toggles the inline create form (rendered by the parent).
 */
import type { Assistant } from "@/lib/api";
import { IconPlus } from "@tabler/icons-react";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Combobox } from "@/components/ui/combobox";
import { LABEL_CLS, HINT_CLS } from "./types";

interface Props {
  value: string;
  assistants: Assistant[];
  onChange: (id: string) => void;
  onNewClick: () => void;
}

function assistantCustomer(a: Assistant): string {
  return a.name || a.metadata?.customer || "unnamed";
}

/** Full dropdown label: "Customer - Industry - Owner" (empty parts dropped). */
function assistantLabel(a: Assistant): string {
  const m = a.metadata || {};
  return [assistantCustomer(a), m.industry, m.owner_name].filter(Boolean).join(" - ");
}

/** A small logo icon for the option (img for URL/data, else emoji, else nothing). */
function logoIcon(logo?: string) {
  const v = (logo || "").trim();
  if (!v) return null;
  return /^(https?:|data:)/i.test(v) ? (
    <img src={v} alt="" className="h-5 w-5 rounded object-contain" />
  ) : (
    <span className="text-base leading-none">{v}</span>
  );
}

export function AssistantSelect({ value, assistants, onChange, onNewClick }: Props) {
  const options = assistants.map((a) => ({
    value: a.assistant_id,
    label: assistantLabel(a),
    short: assistantCustomer(a),
    icon: logoIcon(a.metadata?.logo),
  }));
  return (
    <div className="flex flex-col gap-1.5">
      <Label className={LABEL_CLS}>
        Assistant name{" "}
        <span className={HINT_CLS}>(project will be set to the customer name)</span>
      </Label>
      <Combobox
        options={options}
        value={value}
        onChange={onChange}
        placeholder="Select an assistant…"
        searchPlaceholder="Filter assistants…"
        emptyText="No assistants."
      />
      <div className="flex gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="text-primary"
          onClick={onNewClick}
        >
          <IconPlus size={15} className="mr-1" /> New
        </Button>
      </div>
    </div>
  );
}
