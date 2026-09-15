import type { ToolCall } from "@/lib/api";
import type { ChipData } from "./ToolChip";
import { chipArgSummary, chipCode } from "./helpers";

export class ToolActivity {
  private readonly chips = new Map<string, ChipData>();
  private readonly includeCode: boolean;

  constructor({ includeCode = false }: { includeCode?: boolean } = {}) {
    this.includeCode = includeCode;
  }

  upsert(call: ToolCall): boolean {
    const id = call.id;
    if (!id) return false;
    const name = call.name || "";
    const args = call.args || {};
    const previous = this.chips.get(id);
    const arg = chipArgSummary(name, args);
    const code = this.includeCode ? chipCode(name, args) : undefined;
    this.chips.set(id, previous
      ? { ...previous, arg, ...(code && { code: code.code, codeLang: code.lang }) }
      : {
          id, name, arg, result: null,
          ...(this.includeCode && { code: code?.code, codeLang: code?.lang }),
        });
    return !previous;
  }

  complete(id: string | undefined, result: string): boolean {
    const chip = id ? this.chips.get(id) : undefined;
    if (!chip) return false;
    this.chips.set(chip.id, { ...chip, result });
    return true;
  }

  resume(chips: readonly ChipData[]): void {
    for (const chip of chips) {
      if (chip.result === null) this.chips.set(chip.id, { ...chip, stopped: false });
    }
  }

  codeFor(id: string): string {
    return this.chips.get(id)?.code || "";
  }

  snapshot(): ChipData[] {
    return Array.from(this.chips.values(), (chip) => ({ ...chip }));
  }
}

export function freezePendingChips(chips: readonly ChipData[]): ChipData[] {
  return chips.map((chip) => chip.result === null ? { ...chip, stopped: true } : chip);
}
