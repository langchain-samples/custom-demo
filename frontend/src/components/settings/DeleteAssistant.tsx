/**
 * Danger action (section 5): delete the active assistant after a confirm.
 * There is intentionally no "Reset to defaults".
 */
import { Button } from "@/components/ui/button";

interface Props {
  label: string;
  disabled?: boolean;
  onDelete: () => void;
}

export function DeleteAssistant({ label, disabled, onDelete }: Props) {
  return (
    <div className="mt-2 flex border-t border-border pt-3">
      <Button
        type="button"
        variant="destructive"
        size="sm"
        className="ml-auto"
        disabled={disabled}
        onClick={() => {
          if (
            window.confirm(
              `Delete assistant "${label}"?\n\nThis also deletes its LangSmith trace ` +
                `project, its Prompt/Context Hub prompt, and any skills it created. ` +
                `This cannot be undone.`,
            )
          ) {
            onDelete();
          }
        }}
      >
        Delete assistant
      </Button>
    </div>
  );
}
