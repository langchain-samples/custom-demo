/**
 * The host's palette, in the variables SEP-1865 standardizes.
 *
 * A host OFFERS its theme and a view may take it or keep its own, so this is
 * a courtesy rather than a contract. It lives apart from any one card because
 * it is a fact about this SPA's tokens, not about MCP Apps.
 */

/**
 * The subset of the standardized theme variables we can answer honestly.
 *
 * Keys are from the Theming section of SEP-1865; values are the SPA tokens they
 * come from. Only variables we actually have are sent: the spec has views fall
 * back to their own defaults for anything omitted, so a partial set degrades
 * cleanly and an invented one would not.
 *
 * `inverse` is the odd one. The standardized set has no brand or accent token,
 * and inverse is where a high-contrast primary action surface belongs, so the
 * brand colour goes there and an app's primary button picks it up.
 */
const THEME_VARIABLES: Record<string, string> = {
  "--color-background-primary": "--background",
  "--color-background-secondary": "--panel",
  "--color-background-tertiary": "--panel-2",
  "--color-text-primary": "--foreground",
  "--color-text-secondary": "--muted-foreground",
  "--color-border-primary": "--border",
  "--color-background-inverse": "--brand-primary",
  "--color-text-inverse": "--brand-fg",
  "--border-radius-md": "--radius-md",
};

/** The host's theme, read off the live document rather than guessed. */
export function themeVariables(): Record<string, string> {
  const styles = getComputedStyle(document.documentElement);
  const out: Record<string, string> = {};
  for (const [standard, token] of Object.entries(THEME_VARIABLES)) {
    const value = styles.getPropertyValue(token).trim();
    if (value) out[standard] = value;
  }

  const font = getComputedStyle(document.body).fontFamily;
  if (font) out["--font-sans"] = font;
  return out;
}
