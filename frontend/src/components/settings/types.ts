/**
 * Shared shapes for the settings panel sub-parts. The `PanelConfig` mirrors the
 * editable fields of the original SPA's `dashboardConfig`, split into branding
 * (persisted to the assistant's metadata) and agent config (per-run context).
 */
import type { QuickAction } from "@/lib/api";
import type { Theme } from "@/lib/theme";

export type { QuickAction };

/** System-prompt source: a Prompt Hub handle ("hub") or inline text ("inline"). */
export type PromptMode = "hub" | "inline";

/** All editable panel fields for the active assistant. */
export interface PanelConfig {
  /** LangSmith workspace id (trace routing + workspace-scoped prompts). */
  lsWorkspace: string;
  /** Branding: display name shown in the header. */
  name: string;
  /** Branding: accent hex (drives --brand-blue). */
  accent: string;
  /** Branding: optional secondary hex (drives the 2nd chart series). */
  accent2: string;
  /** Branding: hue that tints surfaces ("" = follow the primary accent). */
  brandNeutral: string;
  /** Branding: surface tint strength, 0–20 (percent). 0 = the plain grey shell. */
  brandTint: number;
  /** Branding: emoji or image URL. */
  logo: string;
  /** Branding: quick-action presets shown in the chat pane. */
  actions: QuickAction[];
  /** Branding: brand-appropriate default theme (light/dark). */
  theme: Theme;
  /** Branding: Google Fonts family for headings ("" = use the bundled fallback). */
  fontHeading: string;
  /** Branding: bundled family headings fall back to. */
  fontHeadingFallback: string;
  /** Branding: Google Fonts family for body text. */
  fontBody: string;
  /** Branding: bundled family body text falls back to. */
  fontBodyFallback: string;
  /** Branding: "curated" never contacts the font CDN. */
  fontSource: "google" | "curated";
  /** Agent config: which system-prompt source is active. */
  promptMode: PromptMode;
  /** Agent config: selected Prompt Hub handle ("" = none). */
  promptName: string;
  /** Agent config: inline system prompt text. */
  systemPrompt: string;
  /** Agent config: the withheld-data "gap" the agent must not fabricate. */
  dataGap: string;
  /** Agent config: advanced synthetic-data-source prompt override. */
  dataPrompt: string;
  /**
   * Agent config: catalogue tool ids this assistant exposes. `null` means the
   * assistant has no saved selection (backend defaults apply); `[]` means every
   * optional tool is off. The two are NOT interchangeable.
   */
  enabledTools: string[] | null;
}

/** Shared field-label typography (uppercase micro-label), matching the SPA. */
export const LABEL_CLS =
  "text-[11px] font-bold uppercase tracking-[0.03em] text-muted-foreground";

/** Inline hint suffix styling (lower-case, muted). */
export const HINT_CLS =
  "text-[10px] font-normal normal-case tracking-normal text-muted-foreground";
