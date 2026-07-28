/**
 * A labelled colour input: native swatch + hex text, kept in sync.
 *
 * Extracted because the swatch/text pair is used for every brand colour, and the
 * two inline copies had already drifted apart in their fallbacks.
 */
import type { ReactNode } from "react";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { normalizeHex } from "@/lib/branding";
import { LABEL_CLS, HINT_CLS } from "./types";

interface Props {
  label: string;
  /** Muted suffix after the label (e.g. "optional"). */
  hint?: ReactNode;
  value: string;
  /** Swatch colour when `value` is blank or unparseable. */
  fallback: string;
  placeholder?: string;
  onChange: (v: string) => void;
}

export function ColorField({ label, hint, value, fallback, placeholder, onChange }: Props) {
  // normalizeHex accepts 3-, 6- and 8-digit forms, so a pasted "#abc" or
  // "#0072BCFF" still drives the swatch instead of silently reverting.
  const swatch = normalizeHex(value) || fallback;
  return (
    <div className="flex flex-col gap-1.5">
      <Label className={LABEL_CLS}>
        {label}
        {hint ? <span className={" " + HINT_CLS}> {hint}</span> : null}
      </Label>
      <div className="flex items-center gap-2">
        <input
          type="color"
          aria-label={label}
          value={swatch}
          onChange={(e) => onChange(e.target.value)}
          className="h-8 w-10 shrink-0 cursor-pointer rounded-lg border border-input bg-transparent p-0"
        />
        <Input
          placeholder={placeholder ?? fallback}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      </div>
    </div>
  );
}
