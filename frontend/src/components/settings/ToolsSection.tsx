/**
 * TOOLS (section 5) — which capabilities this assistant exposes.
 *
 * The catalogue comes from the backend registry (GET /tools), so adding a
 * capability server-side needs no change here. Rows are grouped by the registry's
 * `group`. `always_on` tools render checked+disabled (the dashboard depends on
 * push_widget), and the selection is persisted onto the assistant's context by
 * the parent — unlike the other agent-config fields, which are run-only.
 *
 * `enabled === null` means "no saved selection": rows show the registry defaults,
 * and the first toggle materializes a full explicit array.
 */
import type { ToolSpec } from "@/lib/api";
import { IconAlertTriangle } from "@tabler/icons-react";
import { Switch } from "@/components/ui/switch";
import { CollapseSection } from "./CollapseSection";
import { HINT_CLS } from "./types";

interface Props {
  specs: ToolSpec[];
  enabled: string[] | null;
  onChange: (ids: string[]) => void;
  /** Start expanded (the create form, where the choice is part of the flow). */
  defaultOpen?: boolean;
}

/** Whether a row shows as on, honoring registry defaults when nothing is saved. */
function isOn(spec: ToolSpec, enabled: string[] | null): boolean {
  if (spec.always_on) return true;
  if (enabled === null) return spec.default_on;
  return enabled.includes(spec.id);
}

export function ToolsSection({ specs, enabled, onChange, defaultOpen }: Props) {
  if (!specs.length) return null;

  // Preserve registry order within each group, and group order by first appearance.
  const groups: Array<[string, ToolSpec[]]> = [];
  for (const spec of specs) {
    const found = groups.find(([g]) => g === spec.group);
    if (found) found[1].push(spec);
    else groups.push([spec.group, [spec]]);
  }

  const toggle = (spec: ToolSpec, on: boolean) => {
    if (spec.always_on) return;
    // Materialize the current effective selection before editing it, so the
    // first toggle doesn't silently drop the defaults.
    const base = specs.filter((s) => isOn(s, enabled)).map((s) => s.id);
    const next = on ? [...new Set([...base, spec.id])] : base.filter((id) => id !== spec.id);
    onChange(next);
  };

  const dataSearchOff = specs.some((s) => s.id === "datasearch" && !isOn(s, enabled));
  const count = specs.filter((s) => isOn(s, enabled)).length;

  return (
    <CollapseSection title={`Tools (${count}/${specs.length})`} defaultOpen={defaultOpen}>
      <div className="flex flex-col gap-3">
        {dataSearchOff && (
          <div className="flex items-start gap-1.5 rounded-lg border border-border bg-panel-2 px-2 py-1.5 text-[11px] leading-snug text-muted-foreground">
            <IconAlertTriangle size={13} className="mt-px shrink-0" />
            <span>
              Data search is off - the agent has no grounded data source and will
              answer from the conversation only.
            </span>
          </div>
        )}

        {groups.map(([group, rows]) => (
          <div key={group} className="flex flex-col gap-2">
            <div className={HINT_CLS}>{group}</div>
            {rows.map((spec) => (
              <label
                key={spec.id}
                className="flex cursor-pointer items-start gap-2.5"
                title={spec.always_on ? "Always on - the dashboard depends on it" : undefined}
              >
                <Switch
                  className="mt-0.5 shrink-0"
                  checked={isOn(spec, enabled)}
                  disabled={spec.always_on}
                  onCheckedChange={(v) => toggle(spec, !!v)}
                />
                <span className="min-w-0">
                  <span className="block text-[12.5px] font-medium text-foreground">
                    {spec.label}
                    {spec.always_on && <span className={" " + HINT_CLS}> always on</span>}
                  </span>
                  <span className="block text-[11px] leading-snug text-muted-foreground">
                    {spec.description}
                  </span>
                </span>
              </label>
            ))}
          </div>
        ))}
      </div>
    </CollapseSection>
  );
}
