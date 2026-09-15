/**
 * The card shown while an MCP server is waiting for input mid-tool-call.
 *
 * On the modern MCP spec a server can stop part-way through a tool call and ask
 * for something. `langchain.mcp` turns that round into a LangGraph `interrupt()`,
 * so it reaches us exactly like our own HITL pauses do, and answering it resumes
 * the run: the server re-runs the tool with the answer attached and carries on.
 *
 * The UI is a form built from the request's JSON schema. Every MCP server gets
 * that for free, which is what matters: elicitation is a protocol feature any
 * server may use, and most ship no UI at all.
 *
 * An MCP App is a different thing and renders elsewhere, in `McpAppCard`. Apps
 * are bound to a tool RESULT, so they appear once a call has finished, whereas
 * this card exists only while one is paused. Do not put an app here: nothing can
 * enter the conversation mid-interrupt, so an app that called a tool or sent a
 * message would be talking into a run that cannot hear it.
 */
import { useCallback, useMemo, useState } from "react";
import { IconPlugConnected, IconExternalLink } from "@tabler/icons-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  type JsonSchema,
  type McpElicitationRequest,
  type McpElicitationResponse,
  type McpServerConfig,
  type ReviewInterrupt,
} from "@/lib/api";

const LABEL = "text-[11px] font-bold uppercase tracking-[0.03em] text-muted-foreground";

interface Props {
  review: ReviewInterrupt;
  busy?: boolean;
  /** MCP servers on the active assistant, used to name the one that is asking. */
  servers: McpServerConfig[];
  /** Resume the run. The value is the `{responses: {...}}` the adapter expects. */
  onApprove: (value: Record<string, unknown>) => void;
}

/** Every answer keyed by its request key, in the shape `langchain.mcp` resumes with. */
function resumeWith(answers: Record<string, McpElicitationResponse>) {
  return { responses: answers };
}

/** A readable label for a schema property, falling back to the raw key. */
function fieldLabel(key: string, schema: JsonSchema): string {
  return schema.title || key.replace(/_/g, " ");
}

/* --------------------------- The generic form ---------------------------- */

function SchemaField({
  name,
  schema,
  value,
  onChange,
}: {
  name: string;
  schema: JsonSchema;
  value: string;
  onChange: (v: string) => void;
}) {
  const options = Array.isArray(schema.enum) ? schema.enum.map(String) : [];
  const long = schema.type === "string" && !options.length && !schema.format
    && (schema.description || "").length > 60;

  return (
    <div className="flex flex-col gap-1">
      <Label className={LABEL} htmlFor={`mcp-${name}`}>
        {fieldLabel(name, schema)}
      </Label>
      {schema.description && (
        <span className="text-[11px] leading-snug text-muted-foreground">{schema.description}</span>
      )}
      {options.length ? (
        <div className="flex flex-wrap gap-1.5">
          {options.map((option) => (
            <label
              key={option}
              className={`cursor-pointer rounded-lg border px-2.5 py-1 text-[12.5px] transition-colors ${
                value === option
                  ? "border-brand bg-brand/10 text-foreground"
                  : "border-border hover:bg-panel"
              }`}
            >
              <input
                type="radio"
                className="sr-only"
                name={`mcp-${name}`}
                checked={value === option}
                onChange={() => onChange(option)}
              />
              {option}
            </label>
          ))}
        </div>
      ) : long ? (
        <Textarea
          id={`mcp-${name}`}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="min-h-[70px] font-normal leading-relaxed"
        />
      ) : (
        <Input
          id={`mcp-${name}`}
          // The schema's own hints are enough to pick a sane control: a date
          // field gets a date picker, a number gets a numeric keypad.
          type={
            schema.format === "date"
              ? "date"
              : schema.format === "date-time"
                ? "datetime-local"
                : schema.type === "number" || schema.type === "integer"
                  ? "number"
                  : "text"
          }
          value={value}
          onChange={(e) => onChange(e.target.value)}
          autoComplete="off"
        />
      )}
    </div>
  );
}

function SchemaForm({
  request,
  busy,
  onSubmit,
  onDecline,
}: {
  request: McpElicitationRequest;
  busy?: boolean;
  onSubmit: (content: Record<string, unknown>) => void;
  onDecline: () => void;
}) {
  // Memoized on the request itself: `requested_schema || {}` is a fresh object
  // every render, so deriving from it directly re-seeds the form state.
  const properties = useMemo(() => request.requested_schema?.properties || {}, [request]);
  const required = useMemo(() => new Set(request.requested_schema?.required || []), [request]);

  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      Object.entries(properties).map(([k, s]) => [k, s.default == null ? "" : String(s.default)]),
    ),
  );

  const missing = Object.keys(properties).some((k) => required.has(k) && !values[k]?.trim());
  // A schema with no required list still needs SOMETHING, or the server gets an
  // empty object it asked a question to avoid.
  const empty = !Object.values(values).some((v) => v.trim());

  const submit = () => {
    const content: Record<string, unknown> = {};
    for (const [key, spec] of Object.entries(properties)) {
      const raw = values[key];
      if (raw === undefined || raw === "") continue;
      content[key] =
        spec.type === "number" || spec.type === "integer" ? Number(raw)
        : spec.type === "boolean" ? raw === "true"
        : raw;
    }
    onSubmit(content);
  };

  return (
    <div className="flex flex-col gap-2.5">
      {Object.entries(properties).map(([key, spec]) => (
        <SchemaField
          key={key}
          name={key}
          schema={spec}
          value={values[key] ?? ""}
          onChange={(v) => setValues((prev) => ({ ...prev, [key]: v }))}
        />
      ))}
      <div className="flex items-center gap-2">
        <Button size="sm" disabled={busy || missing || empty} onClick={submit}>
          {busy ? "Sending…" : "Send to the server"}
        </Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={onDecline}>
          Skip
        </Button>
      </div>
    </div>
  );
}

/* --------------------------------- Shell --------------------------------- */

export function McpElicitationCard({
  review,
  busy,
  servers,
  onApprove,
}: Props) {
  // Memoized so `answer` below keeps a stable identity across renders.
  const requests = useMemo(() => review.requests || [], [review]);
  const toolName = String(review.tool_name || "");
  // Only the first request is rendered. A round carries one in practice, and the
  // rest are declined below so the resume is still complete.
  const first: McpElicitationRequest | undefined = requests[0];

  const answer = useCallback(
    (response: McpElicitationResponse) => {
      if (!first) return;
      // Every request in the round needs an answer or the server rejects the
      // resume; anything we did not render is declined rather than left out.
      const answers: Record<string, McpElicitationResponse> = { [first.key]: response };
      for (const extra of requests.slice(1)) answers[extra.key] = { action: "decline" };
      onApprove(resumeWith(answers));
    },
    [first, requests, onApprove],
  );

  if (!first) return null;

  const server = servers.find((s) => !!s.id && toolName.startsWith(`${s.id}_`));

  return (
    <div className="flex animate-in flex-col gap-2.5 rounded-xl border border-brand/40 bg-panel-2 p-3 duration-200 fade-in slide-in-from-bottom-1">
      <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-brand">
        <IconPlugConnected size={13} />
        {server?.label || "Connected system"} needs input
        <span className="ml-auto font-normal normal-case tracking-normal text-muted-foreground">
          {toolName}
        </span>
      </div>

      {first.message && (
        <p className="m-0 text-sm leading-relaxed text-foreground">{first.message}</p>
      )}

      {first.mode === "url" ? (
        <div className="flex flex-col gap-2">
          <a
            href={first.url}
            target="_blank"
            rel="noreferrer noopener"
            className="inline-flex w-fit items-center gap-1.5 text-[13px] text-brand underline"
          >
            <IconExternalLink size={13} /> Open the link the server asked for
          </a>
          <div className="flex items-center gap-2">
            <Button size="sm" disabled={busy} onClick={() => answer({ action: "accept" })}>
              {busy ? "Continuing…" : "I have done that"}
            </Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => answer({ action: "decline" })}>
              Skip
            </Button>
          </div>
        </div>
      ) : (
        <SchemaForm
          request={first}
          busy={busy}
          onSubmit={(content) => answer({ action: "accept", content })}
          onDecline={() => answer({ action: "decline" })}
        />
      )}
    </div>
  );
}
