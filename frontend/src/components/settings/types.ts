/** Shared presentation types and styles for assistant settings. */
import type { ToolSpec } from "@/lib/api";

export type { QuickAction } from "@/lib/api";

/** Shared field-label typography. */
export const LABEL_CLS =
  "text-[11px] font-bold uppercase tracking-[0.03em] text-muted-foreground";

/** Inline hint suffix styling. */
export const HINT_CLS =
  "text-[10px] font-normal normal-case tracking-normal text-muted-foreground";

/** The catalogue owns defaults for an untouched tool selection. */
export function defaultEnabled(specs: ToolSpec[]): string[] {
  return specs.filter((s) => s.default_on || s.always_on).map((s) => s.id);
}
