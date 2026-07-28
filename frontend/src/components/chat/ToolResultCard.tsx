/**
 * Typed renderers for capability-tool results.
 *
 * The simulated tools (draft_email, suggest_meeting_times, list_data_sources,
 * web_search) each return a fixed JSON shape. Rather than showing raw JSON in a
 * collapsed chip, we render a proper card per tool. Anything without a renderer —
 * or whose payload doesn't match — falls back to the chip's pretty-printed JSON,
 * so an off-shape model response degrades instead of breaking.
 *
 * Registry is keyed by tool name, mirroring TOOL_META in ./helpers.
 */
import type { ReactNode } from "react";
import {
  IconCalendarEvent,
  IconCircleCheck,
  IconExternalLink,
  IconMail,
} from "@tabler/icons-react";

/* ------------------------------- Shapes -------------------------------- */

interface EmailDraft {
  to?: string;
  cc?: string;
  subject?: string;
  body?: string;
}
interface MeetingSlot {
  start?: string;
  end?: string;
  label?: string;
  rationale?: string;
}
interface DataSource {
  name?: string;
  type?: string;
  status?: string;
  last_synced?: string;
  record_count?: string;
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

/** Status dot colour by connection state. Uses semantic tokens, not literals. */
function statusClass(status: string): string {
  const s = status.toLowerCase();
  if (s.includes("degrad") || s.includes("error") || s.includes("fail")) return "text-danger";
  if (s.includes("sync")) return "text-warning";
  return "text-success";
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

function MeetingCard({ slots, timezone }: { slots: MeetingSlot[]; timezone: string }) {
  return (
    <Card>
      <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        <IconCalendarEvent size={13} /> Suggested times
        {timezone && <span className="font-normal normal-case"> · {timezone}</span>}
      </div>
      <div className="flex flex-col gap-1.5">
        {slots.map((s, i) => (
          <div
            key={i}
            className="flex items-start gap-2 rounded-md border border-border px-2 py-1.5"
          >
            <IconCircleCheck size={14} className="mt-px shrink-0 text-brand" />
            <span className="min-w-0">
              <span className="block font-medium">{str(s.label) || str(s.start)}</span>
              {str(s.rationale) && (
                <span className="block text-[11px] text-muted-foreground">
                  {str(s.rationale)}
                </span>
              )}
            </span>
          </div>
        ))}
      </div>
    </Card>
  );
}

function SourcesCard({ sources }: { sources: DataSource[] }) {
  return (
    <Card>
      <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        Connected data sources
      </div>
      <div className="flex flex-col gap-1">
        {sources.map((s, i) => (
          <div key={i} className="flex items-baseline gap-2 border-b border-border/50 py-1 last:border-0">
            <span className={"shrink-0 text-[9px] " + statusClass(str(s.status))}>●</span>
            <span className="min-w-0 flex-1 truncate font-medium">{str(s.name)}</span>
            <span className="shrink-0 text-[11px] text-muted-foreground">{str(s.type)}</span>
            {str(s.record_count) && (
              <span className="shrink-0 text-[11px] text-muted-foreground">
                {str(s.record_count)}
              </span>
            )}
          </div>
        ))}
      </div>
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
  suggest_meeting_times: (d) => {
    const slots = arr<MeetingSlot>(d.slots);
    return slots.length ? <MeetingCard slots={slots} timezone={str(d.timezone)} /> : null;
  },
  list_data_sources: (d) => {
    const sources = arr<DataSource>(d.sources);
    return sources.length ? <SourcesCard sources={sources} /> : null;
  },
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
