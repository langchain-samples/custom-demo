/**
 * Human-in-the-loop review editors.
 *
 * When `draft_email` / `suggest_meeting_times` generate something, the tool calls
 * `interrupt()` and the run PAUSES. The payload arrives on the stream's `updates`
 * event and lands here; whatever the user approves is sent back as the resume
 * value, and the tool returns *that* — so the agent genuinely sees the human's
 * edits (its final answer reflects them).
 *
 * Editing is local state and only escapes on Approve, so a slow typist can't have
 * a half-typed subject line submitted by a re-render.
 */
import { useState } from "react";
import { IconCalendarEvent, IconHelpCircle, IconMail, IconPencil } from "@tabler/icons-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import type { ReviewInterrupt } from "@/lib/api";

const LABEL = "text-[11px] font-bold uppercase tracking-[0.03em] text-muted-foreground";

interface Props {
  review: ReviewInterrupt;
  busy?: boolean;
  /** Send the approved value back as the interrupt's resume value. */
  onApprove: (value: Record<string, unknown>) => void;
}

/* ------------------------------- Email ---------------------------------- */

function EmailReview({ review, busy, onApprove }: Props) {
  const d = review.draft as Record<string, string>;
  const [to, setTo] = useState(d.to || "");
  const [cc, setCc] = useState(d.cc || "");
  const [subject, setSubject] = useState(d.subject || "");
  const [body, setBody] = useState(d.body || "");

  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex flex-col gap-1">
        <Label className={LABEL}>To</Label>
        <Input value={to} onChange={(e) => setTo(e.target.value)} autoComplete="off" />
      </div>
      <div className="flex flex-col gap-1">
        <Label className={LABEL}>Cc</Label>
        <Input value={cc} onChange={(e) => setCc(e.target.value)} autoComplete="off" />
      </div>
      <div className="flex flex-col gap-1">
        <Label className={LABEL}>Subject</Label>
        <Input value={subject} onChange={(e) => setSubject(e.target.value)} autoComplete="off" />
      </div>
      <div className="flex flex-col gap-1">
        <Label className={LABEL}>Body</Label>
        <Textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          className="min-h-[180px] font-normal leading-relaxed"
        />
      </div>
      <Button
        size="sm"
        disabled={busy}
        onClick={() => onApprove({ to, cc, subject, body })}
        className="self-start"
      >
        {busy ? "Sending…" : "Approve & continue"}
      </Button>
    </div>
  );
}

/* ------------------------------ Meetings -------------------------------- */

interface Slot {
  start?: string;
  end?: string;
  label?: string;
  rationale?: string;
}

/** ISO-8601 (with offset) → the `YYYY-MM-DDTHH:mm` datetime-local input wants. */
function toLocalInput(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fromLocalInput(value: string): string {
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toISOString();
}

function prettyLocal(value: string): string {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString(undefined, {
    weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
  });
}

function MeetingReview({ review, busy, onApprove }: Props) {
  const d = review.draft as { timezone?: string; slots?: Slot[] };
  const slots = Array.isArray(d.slots) ? d.slots : [];
  const durationMin = Number(review.duration_minutes) || 30;

  const [picked, setPicked] = useState(0);
  // Custom time starts from the first proposal, so the picker opens somewhere sane.
  const [custom, setCustom] = useState(() => toLocalInput(slots[0]?.start || ""));
  const [useCustom, setUseCustom] = useState(false);

  const approve = () => {
    if (useCustom && custom) {
      const startIso = fromLocalInput(custom);
      const end = new Date(new Date(startIso).getTime() + durationMin * 60_000).toISOString();
      onApprove({
        ...d,
        selected: {
          start: startIso,
          end,
          label: prettyLocal(startIso),
          rationale: "Chosen by the user",
        },
      });
      return;
    }
    const s = slots[picked] || slots[0];
    onApprove({ ...d, selected: { ...s, rationale: s?.rationale || "Confirmed by the user" } });
  };

  return (
    <div className="flex flex-col gap-2.5">
      {d.timezone && (
        <div className="text-[11px] text-muted-foreground">Times shown in {d.timezone}</div>
      )}
      <div className="flex flex-col gap-1.5">
        {slots.map((s, i) => (
          <label
            key={i}
            className={
              "flex cursor-pointer items-start gap-2 rounded-md border px-2 py-1.5 transition-colors " +
              (!useCustom && picked === i ? "border-brand bg-brand/10" : "border-border")
            }
          >
            <input
              type="radio"
              name="slot"
              checked={!useCustom && picked === i}
              onChange={() => {
                setPicked(i);
                setUseCustom(false);
              }}
              className="mt-0.5 accent-[var(--brand-primary)]"
            />
            <span className="min-w-0">
              <span className="block text-[12.5px] font-medium">
                {s.label || prettyLocal(s.start || "")}
              </span>
              {s.rationale && (
                <span className="block text-[11px] text-muted-foreground">{s.rationale}</span>
              )}
            </span>
          </label>
        ))}

        <label
          className={
            "flex cursor-pointer items-center gap-2 rounded-md border px-2 py-1.5 transition-colors " +
            (useCustom ? "border-brand bg-brand/10" : "border-border")
          }
        >
          <input
            type="radio"
            name="slot"
            checked={useCustom}
            onChange={() => setUseCustom(true)}
            className="accent-[var(--brand-primary)]"
          />
          <span className="flex flex-wrap items-center gap-2 text-[12.5px]">
            <span className="inline-flex items-center gap-1">
              <IconPencil size={12} /> Pick another time
            </span>
            <input
              type="datetime-local"
              value={custom}
              onChange={(e) => {
                setCustom(e.target.value);
                setUseCustom(true);
              }}
              className="rounded-md border border-input bg-transparent px-1.5 py-0.5 text-[12px] text-foreground"
            />
            <span className="text-[11px] text-muted-foreground">{durationMin} min</span>
          </span>
        </label>
      </div>

      <Button
        size="sm"
        disabled={busy || (useCustom && !custom)}
        onClick={approve}
        className="self-start"
      >
        {busy ? "Confirming…" : "Confirm time"}
      </Button>
    </div>
  );
}

/* ---------------------------- Ask the user ------------------------------ */

function QuestionReview({ review, busy, onApprove }: Props) {
  // Payload is { kind:"user_question", question, options } — no draft; both ride
  // the ReviewInterrupt index signature.
  const draft = review.draft as Record<string, unknown> | undefined;
  const question = String((review.question as string) ?? draft?.question ?? "");
  const raw = (review.options ?? draft?.options) as unknown;
  const options = (Array.isArray(raw) ? raw : [])
    .map((o) => String(o).trim())
    .filter(Boolean);
  // `ask_user` is multiple choice, but an older/odd payload may carry no options —
  // fall back to a text box rather than stranding the run with nothing to click.
  const [picked, setPicked] = useState<string | null>(null);
  const [typed, setTyped] = useState("");
  const answer = options.length ? (picked ?? "") : typed.trim();

  return (
    <div className="flex flex-col gap-2.5">
      {question && <p className="m-0 text-sm leading-relaxed text-foreground">{question}</p>}
      {options.length ? (
        <div className="flex flex-col gap-1.5">
          {options.map((option) => (
            <label
              key={option}
              className={`flex cursor-pointer items-center gap-2 rounded-lg border px-2.5 py-2 text-[13px] leading-snug transition-colors ${
                picked === option
                  ? "border-brand bg-brand/10 text-foreground"
                  : "border-border hover:bg-panel"
              }`}
            >
              <input
                type="radio"
                name={`answer-${review.kind}-${question}`}
                className="accent-brand"
                checked={picked === option}
                onChange={() => setPicked(option)}
              />
              {option}
            </label>
          ))}
        </div>
      ) : (
        <Textarea
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder="Type your answer…"
          className="min-h-[80px] font-normal leading-relaxed"
          autoFocus
        />
      )}
      <Button
        size="sm"
        disabled={busy || !answer}
        onClick={() => onApprove({ answer })}
        className="self-start"
      >
        {busy ? "Sending…" : "Answer"}
      </Button>
    </div>
  );
}

/* ------------------------------- Shell ---------------------------------- */

const META: Record<string, { icon: typeof IconMail; title: string }> = {
  email_draft: { icon: IconMail, title: "Review before sending" },
  meeting_slots: { icon: IconCalendarEvent, title: "Choose a time" },
  user_question: { icon: IconHelpCircle, title: "A quick question" },
};

export function ReviewCard(props: Props) {
  const meta = META[props.review.kind] || { icon: IconPencil, title: "Review" };
  const Icon = meta.icon;
  const known = props.review.kind in META;

  return (
    <div className="flex animate-in flex-col gap-2.5 rounded-xl border border-brand/40 bg-panel-2 p-3 duration-200 fade-in slide-in-from-bottom-1">
      <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-brand">
        <Icon size={13} /> {meta.title}
        <span className="ml-auto font-normal normal-case tracking-normal text-muted-foreground">
          paused for you
        </span>
      </div>
      {props.review.kind === "meeting_slots" ? (
        <MeetingReview {...props} />
      ) : props.review.kind === "user_question" ? (
        <QuestionReview {...props} />
      ) : known ? (
        <EmailReview {...props} />
      ) : (
        // Unknown interrupt kind: don't strand the run — let the user resume it.
        <div className="flex flex-col gap-2">
          <pre className="m-0 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border bg-background p-2 font-mono text-[11px]">
            {JSON.stringify(props.review.draft, null, 2)}
          </pre>
          <Button size="sm" disabled={props.busy} onClick={() => props.onApprove({})} className="self-start">
            Continue
          </Button>
        </div>
      )}
    </div>
  );
}
