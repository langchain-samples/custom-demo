import { useEffect, useState } from "react";
import { applyBrand } from "@/lib/branding";
import { applyTypography, type FontStatus } from "@/lib/fonts";
import type { AssistantDraft } from "@/lib/assistantSession";

/** Brand effects belong to the active session, not to whether its editor is visible. */
export function useAssistantAppearance(draft: AssistantDraft, hasRecord: boolean, hasSelection: boolean) {
  const [fontStatus, setFontStatus] = useState<{ heading: FontStatus; body: FontStatus }>({
    heading: "curated", body: "curated",
  });
  useEffect(() => {
    applyBrand(hasRecord ? {
      primary: draft.accent, secondary: draft.accent2,
      neutral: draft.brandNeutral, tint: draft.brandTint,
    } : null);
  }, [hasRecord, draft.accent, draft.accent2, draft.brandNeutral, draft.brandTint]);

  useEffect(() => {
    let cancelled = false;
    void applyTypography(hasSelection ? {
      heading: { family: draft.fontHeading, fallback: draft.fontHeadingFallback },
      body: { family: draft.fontBody, fallback: draft.fontBodyFallback },
      source: draft.fontSource,
    } : null).then((status) => {
      if (!cancelled) setFontStatus(status);
    });
    return () => { cancelled = true; };
  }, [hasSelection, draft.fontHeading, draft.fontHeadingFallback, draft.fontBody, draft.fontBodyFallback, draft.fontSource]);
  return fontStatus;
}
