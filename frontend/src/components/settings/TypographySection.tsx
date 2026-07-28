/**
 * TYPOGRAPHY — the customer's typeface, with an honest fallback story.
 *
 * Each slot takes a Google Fonts family to try plus a bundled family to fall
 * back to. The status chip reports what ACTUALLY happened (loaded / unavailable
 * / skipped), which is what makes "CDN with curated fallback" legible instead of
 * mysterious — otherwise a silently-substituted font just looks like a bug.
 */
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { CURATED_FONTS, DEFAULT_CURATED, type FontStatus } from "@/lib/fonts";
import { LABEL_CLS, HINT_CLS } from "./types";

interface SlotProps {
  label: string;
  family: string;
  fallback: string;
  status: FontStatus;
  onFamily: (v: string) => void;
  onFallback: (v: string) => void;
}

const STATUS: Record<FontStatus, { text: string; cls: string }> = {
  loaded: { text: "Loaded from Google Fonts", cls: "text-success" },
  unavailable: { text: "Unavailable — using fallback", cls: "text-warning" },
  invalid: { text: "Invalid name — using fallback", cls: "text-danger" },
  curated: { text: "Using bundled font", cls: "text-muted-foreground" },
};

function Slot({ label, family, fallback, status, onFamily, onFallback }: SlotProps) {
  const s = STATUS[status];
  return (
    <div className="flex flex-col gap-1.5">
      <Label className={LABEL_CLS}>
        {label} <span className={HINT_CLS}>(Google Fonts family)</span>
      </Label>
      <Input
        placeholder="e.g. Poppins — blank to use the bundled font"
        value={family}
        onChange={(e) => onFamily(e.target.value)}
        autoComplete="off"
        data-1p-ignore="true"
      />
      <select
        value={fallback || DEFAULT_CURATED}
        onChange={(e) => onFallback(e.target.value)}
        className="h-8 rounded-lg border border-input bg-transparent px-2 text-[12.5px] text-foreground"
      >
        {CURATED_FONTS.map((f) => (
          <option key={f.id} value={f.id}>
            {f.label}
          </option>
        ))}
      </select>
      <span className={"text-[10px] " + s.cls}>{s.text}</span>
    </div>
  );
}

interface Props {
  headingFont: string;
  headingFallback: string;
  bodyFont: string;
  bodyFallback: string;
  useGoogle: boolean;
  status: { heading: FontStatus; body: FontStatus };
  onHeadingFont: (v: string) => void;
  onHeadingFallback: (v: string) => void;
  onBodyFont: (v: string) => void;
  onBodyFallback: (v: string) => void;
  onUseGoogle: (v: boolean) => void;
}

export function TypographySection({
  headingFont, headingFallback, bodyFont, bodyFallback, useGoogle, status,
  onHeadingFont, onHeadingFallback, onBodyFont, onBodyFallback, onUseGoogle,
}: Props) {
  return (
    <div className="flex flex-col gap-3.5">
      <div className={LABEL_CLS + " border-t border-border pt-3"}>Typography</div>

      <label className="flex cursor-pointer items-center gap-2 text-[12.5px] text-foreground">
        <input
          type="checkbox"
          checked={useGoogle}
          onChange={(e) => onUseGoogle(e.target.checked)}
          className="h-3.5 w-3.5 accent-[var(--brand-primary)]"
        />
        Load brand fonts from Google Fonts
      </label>
      <p className="m-0 -mt-2 text-[10px] leading-snug text-muted-foreground">
        Off keeps the app fully self-hosted — no third-party request.
      </p>

      <Slot
        label="Heading font"
        family={headingFont}
        fallback={headingFallback}
        status={status.heading}
        onFamily={onHeadingFont}
        onFallback={onHeadingFallback}
      />
      <Slot
        label="Body font"
        family={bodyFont}
        fallback={bodyFallback}
        status={status.body}
        onFamily={onBodyFont}
        onFallback={onBodyFallback}
      />
    </div>
  );
}
