/**
 * Required workspace picker (section 1). Populated from GET /workspaces; a saved
 * id not in the list stays selectable (matching the SPA). No graph-default
 * option — placeholder shows until a workspace is chosen.
 */
import type { Workspace } from "@/lib/api";
import { Label } from "@/components/ui/label";
import { Combobox } from "@/components/ui/combobox";
import { LABEL_CLS, HINT_CLS } from "./types";

interface Props {
  value: string;
  workspaces: Workspace[];
  /**
   * The org these workspaces belong to, from GET /workspaces. It was hardcoded
   * here on the assumption of a single-org deployment, which was wrong the
   * moment anyone ran this against their own org: the label then names someone
   * else's. The value is already fetched for the create form, so read it.
   */
  organization?: string;
  onChange: (id: string) => void;
}

export function WorkspaceSelect({ value, workspaces, organization = "", onChange }: Props) {
  const options = workspaces.map((w) => ({ value: w.id, label: w.name || w.id }));
  if (value && !options.some((o) => o.value === value)) options.push({ value, label: value });
  return (
    <div className="flex flex-col gap-1.5">
      <Label className={LABEL_CLS}>
        Workspace
        {organization ? <span className={HINT_CLS}> (Org: {organization})</span> : null}
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
