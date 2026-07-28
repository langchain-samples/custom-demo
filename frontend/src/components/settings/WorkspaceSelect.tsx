/**
 * Required workspace picker (section 1). Populated from GET /workspaces; a saved
 * id not in the list stays selectable (matching the SPA). No graph-default
 * option — placeholder shows until a workspace is chosen.
 */
import type { Workspace } from "@/lib/api";
import { Label } from "@/components/ui/label";
import { Combobox } from "@/components/ui/combobox";
import { LABEL_CLS, HINT_CLS } from "./types";

// Single-org deployment — the org name is fixed, so label it inline rather than
// fetching it per workspace.
const ORG_NAME = "Enterprise Readiness Demos";

interface Props {
  value: string;
  workspaces: Workspace[];
  onChange: (id: string) => void;
}

export function WorkspaceSelect({ value, workspaces, onChange }: Props) {
  const options = workspaces.map((w) => ({ value: w.id, label: w.name || w.id }));
  if (value && !options.some((o) => o.value === value)) options.push({ value, label: value });
  return (
    <div className="flex flex-col gap-1.5">
      <Label className={LABEL_CLS}>
        Workspace <span className={HINT_CLS}>(Org: {ORG_NAME})</span>
      </Label>
      <Combobox
        options={options}
        value={value}
        onChange={onChange}
        placeholder="Select a workspace…"
        searchPlaceholder="Filter workspaces…"
        emptyText="No workspaces."
      />
    </div>
  );
}
