/**
 * Light/dark theme helpers. Dark is applied via the `dark` class on <html>
 * (the CSS in index.css keys `.dark` off it). The user's last-used theme is
 * remembered in localStorage so a reload restores it before assistants load;
 * once an assistant with a `metadata.theme` is active, that brand default wins.
 */
export type Theme = "light" | "dark";

const THEME_KEY = "lgTheme";
const DEFAULT_THEME: Theme = "dark";

/** Toggle the `dark` class on <html> to match `theme`. */
export function applyTheme(theme: Theme): void {
  if (typeof document === "undefined") return;
  document.documentElement.classList.toggle("dark", theme === "dark");
}

/** The remembered theme, or the dark default when none/invalid is stored. */
export function getStoredTheme(): Theme {
  try {
    const v = localStorage.getItem(THEME_KEY);
    return v === "light" || v === "dark" ? v : DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
}

/** Remember the theme for next load. */
export function setStoredTheme(theme: Theme): void {
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    /* ignore quota / privacy-mode errors */
  }
}

/** Normalize an unknown value to a Theme (falls back to the dark default). */
export function coerceTheme(v: unknown): Theme {
  return v === "light" || v === "dark" ? v : DEFAULT_THEME;
}
