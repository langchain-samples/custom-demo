/**
 * Typed renderers for capability-tool results.
 *
 * The simulated tools (draft_email, web_search) each return a fixed JSON shape.
 * Rather than showing raw JSON in a collapsed chip, we render a proper card per
 * tool. Anything without a renderer — or whose payload doesn't match — falls
 * back to the chip's pretty-printed JSON, so an off-shape model response
 * degrades instead of breaking.
 *
 * Registry is keyed by tool name, mirroring TOOL_META in ./helpers.
 */
import type { ReactNode } from "react";
import { IconExternalLink, IconMail } from "@tabler/icons-react";

/* ------------------------------- Shapes -------------------------------- */

interface EmailDraft {
  to?: string;
  cc?: string;
  subject?: string;
  body?: string;
}
interface SearchResult {
  title?: string;
  url?: string;
  snippet?: string;
  published?: string;
}

const str = (v: unknown): string => (typeof v === "string" ? v : "");
const arr = <T,>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);

/* ------------------------------ Primitives ------------------------------ */

function Card({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-col gap-2 rounded-md border border-border bg-background p-2.5 text-[12px] text-foreground">
      {children}
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  if (!value) return null;
  return (
    <div className="flex gap-2">
      <span className="w-12 shrink-0 text-[11px] text-muted-foreground">{label}</span>
      <span className="min-w-0 break-words">{value}</span>
    </div>
  );
}


/* ------------------------------- Renderers ------------------------------ */

function EmailCard({ d }: { d: EmailDraft }) {
  return (
    <Card>
      <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        <IconMail size={13} /> Draft email
      </div>
      <Field label="To" value={str(d.to)} />
      <Field label="Cc" value={str(d.cc)} />
      <Field label="Subject" value={str(d.subject)} />
      {str(d.body) && (
        <p className="m-0 whitespace-pre-wrap border-t border-border pt-2 leading-relaxed">
          {str(d.body)}
        </p>
      )}
    </Card>
  );
}

function SearchCard({ results }: { results: SearchResult[] }) {
  return (
    <Card>
      <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        Web results
      </div>
      <div className="flex flex-col gap-2">
        {results.map((r, i) => (
          <div key={i} className="min-w-0">
            <a
              href={str(r.url) || undefined}
              target="_blank"
              rel="noreferrer noopener"
              className="inline-flex items-center gap-1 font-medium text-[color:var(--brand-label)] hover:underline"
            >
              <span className="min-w-0 break-words">{str(r.title) || str(r.url)}</span>
              <IconExternalLink size={11} className="shrink-0" />
            </a>
            {str(r.snippet) && (
              <p className="m-0 text-[11px] leading-snug text-muted-foreground">
                {str(r.snippet)}
              </p>
            )}
            {str(r.published) && (
              <span className="text-[10px] text-muted-foreground">{str(r.published)}</span>
            )}
          </div>
        ))}
      </div>
    </Card>
  );
}

/* -------------------------------- Registry ------------------------------ */

type Payload = Record<string, unknown>;

const RENDERERS: Record<string, (d: Payload) => ReactNode | null> = {
  draft_email: (d) =>
    d.subject || d.body ? <EmailCard d={d as EmailDraft} /> : null,
  web_search: (d) => {
    const results = arr<SearchResult>(d.results);
    return results.length ? <SearchCard results={results} /> : null;
  },
};

/** True when this tool has a typed card (so the chip can auto-expand it). */
export function hasToolCard(name: string): boolean {
  return name in RENDERERS;
}

/**
 * Render a tool's result as a typed card, or null to fall back to raw JSON.
 * Never throws on malformed input — an off-shape payload returns null.
 */
export function renderToolResult(name: string, raw: string): ReactNode | null {
  const render = RENDERERS[name];
  if (!render) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
  const payload = parsed as Payload;
  if (typeof payload.error === "string") return null; // show the raw error instead
  try {
    return render(payload);
  } catch {
    return null;
  }
}
