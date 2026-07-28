/**
 * BRAND — the colour half of per-customer theming.
 *
 * Primary and secondary seed everything else; "surface tint" is what makes the
 * whole shell read as the customer's rather than a grey app with a coloured
 * button. Tint 0 reproduces the original palette exactly, so it doubles as the
 * kill switch.
 *
 * The chart swatches are read back from the resolved `--chart-*` tokens, so what
 * you see here is literally what a chart will draw.
 */
import { useEffect, useState } from "react";
import { Label } from "@/components/ui/label";
import { ColorField } from "./ColorField";
import { CollapseSection } from "./CollapseSection";
import { LABEL_CLS, HINT_CLS } from "./types";
import {
  DEFAULT_PRIMARY,
  DEFAULT_SECONDARY,
  MAX_TINT,
  contrastForeground,
  contrastRatio,
  normalizeHex,
  resolveColor,
} from "@/lib/branding";

interface Props {
  accent: string;
  accent2: string;
  neutral: string;
  tint: number;
  onAccent: (v: string) => void;
  onAccent2: (v: string) => void;
  onNeutral: (v: string) => void;
  onTint: (v: number) => void;
}

export function BrandSection({
  accent, accent2, neutral, tint,
  onAccent, onAccent2, onNeutral, onTint,
}: Props) {
  // Re-read the derived series whenever a seed changes (applyBrand has already
  // run and cleared the resolve cache by the time this effect fires).
  const [series, setSeries] = useState<string[]>([]);
  useEffect(() => {
    setSeries([1, 2, 3, 4, 5, 6, 7, 8].map((i) => resolveColor(`--chart-${i}`)));
  }, [accent, accent2, neutral, tint]);

  const primary = normalizeHex(accent) || DEFAULT_PRIMARY;
  const ratio = contrastRatio(contrastForeground(primary), primary);

  return (
    <div className="flex flex-col gap-3.5">
      <div className={LABEL_CLS + " border-t border-border pt-3"}>Brand</div>

      <ColorField
        label="Primary color"
        value={accent}
        fallback={DEFAULT_PRIMARY}
        onChange={onAccent}
      />
      <ColorField
        label="Secondary color"
        hint="(2nd chart series)"
        value={accent2}
        fallback={DEFAULT_SECONDARY}
        placeholder="(none)"
        onChange={onAccent2}
      />

      <CollapseSection title="Advanced brand">
        <div className="flex flex-col gap-3.5">
          <div className="flex flex-col gap-1.5">
            <Label className={LABEL_CLS}>
              Surface tint <span className={HINT_CLS}>({tint}%, 0 is plain grey)</span>
            </Label>
            <input
              type="range"
              min={0}
              max={MAX_TINT}
              step={1}
              value={tint}
              onChange={(e) => onTint(Number(e.target.value))}
              className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-panel-2 accent-[var(--brand-primary)]"
            />
            <p className="m-0 text-[10px] leading-snug text-muted-foreground">
              Tints panels, borders and the background toward the brand hue.
            </p>
          </div>

          <ColorField
            label="Tint hue"
            hint="(defaults to primary)"
            value={neutral}
            fallback={primary}
            placeholder="(follow primary)"
            onChange={onNeutral}
          />
          <p className="m-0 text-[10px] leading-snug text-muted-foreground">
            Set this when the primary is a saturated red or orange — those make an
            unpleasant surface tint. A brand's dark neutral usually works better.
          </p>

          <div className="flex flex-col gap-1.5">
            <Label className={LABEL_CLS}>Chart series</Label>
            <div className="flex gap-1">
              {series.map((c, i) => (
                <span
                  key={i}
                  title={`--chart-${i + 1}: ${c}`}
                  style={{ background: c }}
                  className="h-5 flex-1 rounded border border-border"
                />
              ))}
            </div>
            <p className="m-0 text-[10px] leading-snug text-muted-foreground">
              Derived from the brand pair. Live values — this is what charts draw.
            </p>
          </div>

          <div className="flex items-center justify-between text-[10px] text-muted-foreground">
            <span>Contrast on brand fills</span>
            <span className={ratio >= 4.5 ? "text-success" : "text-danger"}>
              {ratio.toFixed(1)}:1 {ratio >= 4.5 ? "✓" : "(low)"}
            </span>
          </div>
        </div>
      </CollapseSection>
    </div>
  );
}
