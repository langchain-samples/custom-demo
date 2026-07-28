/**
 * Collapsed editor for the assistant's quick-action presets (label + question
 * rows, with an "Add" button). Emits the full array on every edit so the parent can
 * debounce-save it into the assistant's metadata.
 */
import type { QuickAction } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { IconPlus, IconX } from "@tabler/icons-react";
import { CollapseSection } from "./CollapseSection";

interface Props {
  actions: QuickAction[];
  onChange: (actions: QuickAction[]) => void;
}

export function QuickActionsEditor({ actions, onChange }: Props) {
  const update = (i: number, patch: Partial<QuickAction>) =>
    onChange(actions.map((a, idx) => (idx === i ? { ...a, ...patch } : a)));
  const remove = (i: number) => onChange(actions.filter((_, idx) => idx !== i));
  const add = () => onChange([...actions, { label: "", question: "" }]);

  return (
    <CollapseSection title="Quick actions">
      <Button type="button" variant="outline" size="sm" className="w-fit text-primary" onClick={add}>
        <IconPlus size={15} className="mr-1" /> Add
      </Button>
      <div className="flex flex-col gap-2.5">
        {actions.map((a, i) => (
          <div
            key={i}
            className="relative flex flex-col gap-1.5 rounded-xl border border-border p-2.5"
          >
            <button
              type="button"
              title="Remove"
              onClick={() => remove(i)}
              className="absolute right-1.5 top-1.5 text-muted-foreground hover:text-foreground"
            >
              <IconX className="size-3" />
            </button>
            <Input
              placeholder="Button label (e.g. Donor: impact of aid)"
              value={a.label || ""}
              onChange={(e) => update(i, { label: e.target.value })}
            />
            <Input
              placeholder="Question to ask"
              value={a.question || ""}
              onChange={(e) => update(i, { question: e.target.value })}
            />
          </div>
        ))}
      </div>
    </CollapseSection>
  );
}
