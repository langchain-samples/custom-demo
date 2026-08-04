/**
 * Per-customer typography: the brand's real face from Google Fonts, with a
 * curated self-hosted fallback.
 *
 * The stack is always `"<Brand Family>", <curated family>, <system stack>`, so
 * text renders immediately in a sane face and upgrades if/when the CDN family
 * loads. Failure is detected rather than assumed — see `loadGoogleFamily`.
 */

/** Self-hosted families bundled with the app (see the @imports in index.css). */
export const CURATED_FONTS = [
  { id: "Geist Variable", label: "Geist - neutral geometric" },
  { id: "Inter Variable", label: "Inter - neutral UI" },
  { id: "IBM Plex Sans Variable", label: "IBM Plex Sans - humanist / technical" },
  { id: "Space Grotesk Variable", label: "Space Grotesk - display / techy" },
  { id: "Source Serif 4 Variable", label: "Source Serif - editorial / institutional" },
] as const;

export type CuratedFont = (typeof CURATED_FONTS)[number]["id"];
export const DEFAULT_CURATED: CuratedFont = "Geist Variable";

const CURATED_IDS = new Set<string>(CURATED_FONTS.map((f) => f.id));

/**
 * Google Fonts family names are letters, digits and spaces. Excluding
 * `" ; } ( \` means the name can break out of neither the CSS string it lands
 * in nor the URL it is interpolated into. The backend applies the same rule
 * before a family ever reaches assistant metadata.
 */
const FAMILY_RE = /^[A-Za-z0-9][A-Za-z0-9 ]{0,48}$/;

export function isValidFamily(family: string): boolean {
  return FAMILY_RE.test((family || "").trim());
}

/** Outcome of a font request, surfaced in the settings panel. */
export type FontStatus = "curated" | "loaded" | "unavailable" | "invalid";

const attempted = new Map<string, Promise<boolean>>();

function injectLink(href: string, timeoutMs: number): Promise<boolean> {
  return new Promise((resolve) => {
    const existing = document.querySelector<HTMLLinkElement>(`link[data-font-href="${href}"]`);
    if (existing) return resolve(true);
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = href;
    link.dataset.fontHref = href;
    let settled = false;
    const done = (ok: boolean) => {
      if (settled) return;
      settled = true;
      resolve(ok);
    };
    link.onload = () => done(true);
    link.onerror = () => done(false);
    setTimeout(() => done(false), timeoutMs);
    document.head.appendChild(link);
  });
}

/**
 * Try to make `family` renderable, returning whether it actually is.
 *
 * The final `fonts.load()` + `check()` is the step that matters: Google returns
 * HTTP 400 for an unknown family (so the <link> errors), but for a KNOWN family
 * missing a requested weight it silently trims the response — only an explicit
 * load-then-check catches that.
 */
export async function loadGoogleFamily(family: string, timeoutMs = 2500): Promise<boolean> {
  const name = (family || "").trim();
  if (!isValidFamily(name)) return false;
  if (typeof document === "undefined" || !document.fonts) return false;
  // Already available (curated, system-installed, or previously loaded).
  if (document.fonts.check(`16px "${name}"`)) return true;

  const cached = attempted.get(name);
  if (cached) return cached;

  const run = (async () => {
    const href =
      "https://fonts.googleapis.com/css2?family=" +
      encodeURIComponent(name).replace(/%20/g, "+") +
      ":wght@400;500;600;700&display=swap";
    if (!(await injectLink(href, timeoutMs))) return false;
    try {
      await Promise.all([
        document.fonts.load(`400 16px "${name}"`),
        document.fonts.load(`600 16px "${name}"`),
      ]);
    } catch {
      return false;
    }
    return document.fonts.check(`600 16px "${name}"`);
  })();

  attempted.set(name, run);
  return run;
}

/** Build a CSS font stack: brand family (if any) → curated → system tail. */
function stack(family: string, fallback: string): string {
  const curated = CURATED_IDS.has(fallback) ? fallback : DEFAULT_CURATED;
  const tail = `'${curated}', var(--font-curated)`;
  return isValidFamily(family) ? `'${family}', ${tail}` : tail;
}

export interface FontChoice {
  /** The brand's own family to try from Google Fonts. "" to skip the CDN. */
  family?: string;
  /** Bundled family to fall back to. */
  fallback?: string;
}

export interface Typography {
  heading?: FontChoice;
  body?: FontChoice;
  /** "curated" never touches the CDN. */
  source?: "google" | "curated";
}

/**
 * Apply typography to :root and report what actually happened per slot.
 *
 * The stack is set immediately (so the curated face renders at once), then
 * upgraded in place if the CDN family turns out to be available — there is no
 * flash of unstyled text either way because `--font-curated` is always the tail.
 */
export async function applyTypography(
  t: Typography | null,
): Promise<{ heading: FontStatus; body: FontStatus }> {
  if (typeof document === "undefined") return { heading: "curated", body: "curated" };
  const root = document.documentElement.style;

  if (!t) {
    root.removeProperty("--font-body-stack");
    root.removeProperty("--font-heading-stack");
    return { heading: "curated", body: "curated" };
  }

  const slots: Array<["heading" | "body", string, FontChoice]> = [
    ["body", "--font-body-stack", t.body || {}],
    ["heading", "--font-heading-stack", t.heading || {}],
  ];
  const out: { heading: FontStatus; body: FontStatus } = { heading: "curated", body: "curated" };

  for (const [slot, cssVar, choice] of slots) {
    const family = (choice.family || "").trim();
    const fallback = choice.fallback || DEFAULT_CURATED;
    root.setProperty(cssVar, stack(family, fallback));
    if (!family) {
      out[slot] = "curated";
    } else if (!isValidFamily(family)) {
      out[slot] = "invalid";
      root.setProperty(cssVar, stack("", fallback)); // never emit an unvetted name
    } else if (t.source === "curated") {
      out[slot] = "curated";
      root.setProperty(cssVar, stack("", fallback));
    } else {
      out[slot] = (await loadGoogleFamily(family)) ? "loaded" : "unavailable";
      if (out[slot] === "unavailable") root.setProperty(cssVar, stack("", fallback));
    }
  }
  return out;
}
