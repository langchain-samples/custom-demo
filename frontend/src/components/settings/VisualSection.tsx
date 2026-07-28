/**
 * VISUAL branding (section 3): display name, logo (emoji or URL), theme and the
 * collapsed Quick actions editor. Colour lives in BrandSection. All
 * edits are branding → the parent debounce-PATCHes them into the assistant's
 * metadata and reflects them in the header + presets live.
 */
import type { QuickAction } from "@/lib/api";
import type { Theme } from "@/lib/theme";
import { IconSun, IconMoon, IconX } from "@tabler/icons-react";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { QuickActionsEditor } from "./QuickActionsEditor";
import { LABEL_CLS } from "./types";

interface Props {
  name: string;
  logo: string;
  actions: QuickAction[];
  theme: Theme;
  onName: (v: string) => void;
  onLogo: (v: string) => void;
  onActions: (a: QuickAction[]) => void;
  onTheme: (t: Theme) => void;
}

const URL_RE = /^(https?:|data:)/i;

export function VisualSection({
  name,
  logo,
  actions,
  theme,
  onName,
  onLogo,
  onActions,
  onTheme,
}: Props) {
  return (
    <div className="flex flex-col gap-3.5">
      <div className={LABEL_CLS + " border-t border-border pt-3"}>Visual</div>

      <div className="flex flex-col gap-1.5">
        <Label className={LABEL_CLS}>Display name</Label>
        <Input
          placeholder="Dashboard Agent"
          value={name}
          onChange={(e) => onName(e.target.value)}
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <Label className={LABEL_CLS}>Logo (emoji or image URL)</Label>
        {URL_RE.test(logo.trim()) ? (
          <div className="flex items-center gap-2 rounded-lg border border-input bg-panel-2 px-2 py-1.5">
            <img
              src={logo}
              alt="logo"
              className="h-8 w-8 shrink-0 rounded object-contain"
            />
            <span className="flex-1 truncate text-xs text-muted-foreground">{logo}</span>
            <button
              type="button"
              aria-label="Clear logo"
              title="Clear logo"
              onClick={() => onLogo("")}
              className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
            >
              <IconX size={14} />
            </button>
          </div>
        ) : (
          <Input
            placeholder="https://…/logo.png or an emoji"
            value={logo}
            onChange={(e) => onLogo(e.target.value)}
          />
        )}
      </div>

      <div className="flex flex-col gap-1.5">
        <Label className={LABEL_CLS}>Theme</Label>
        <div className="inline-flex w-fit rounded-lg border border-input p-0.5">
          {(["light", "dark"] as const).map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => onTheme(t)}
              className={
                "flex items-center gap-1.5 rounded-md px-3 py-1 text-xs font-semibold capitalize transition-colors " +
                (theme === t
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground")
              }
            >
              {t === "light" ? <IconSun size={14} /> : <IconMoon size={14} />}
              {t}
            </button>
          ))}
        </div>
      </div>

      <QuickActionsEditor actions={actions} onChange={onActions} />
    </div>
  );
}
