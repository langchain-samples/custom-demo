/**
 * The card shown while an MCP server is waiting for input mid-tool-call.
 *
 * On the modern MCP spec a server can stop part-way through a tool call and ask
 * for something. `langchain.mcp` turns that round into a LangGraph `interrupt()`,
 * so it reaches us exactly like our own HITL pauses do, and answering it resumes
 * the run: the server re-runs the tool with the answer attached and carries on.
 *
 * Two ways to render the same pause:
 *
 * 1. **The server's own UI.** If the paused tool is an MCP App, it declares a
 *    `ui://` HTML resource. We fetch that HTML (the deployment reads it over MCP
 *    for us) and run it in a sandboxed iframe. That is the only way to collect
 *    something a form cannot express, like a drawn signature.
 * 2. **A generic form**, built from the request's JSON schema. Every MCP server
 *    gets this for free, which matters because most ship no UI at all.
 *
 * The app is a real MCP App: the conversation with it is SEP-1865, JSON-RPC 2.0
 * over `postMessage`, and it lives in `lib/mcpAppHost.ts`. Nothing the app sends
 * is trusted beyond being shaped into the elicitation answer, which the server
 * itself has to validate against the schema it asked for.
 *
 * The iframe is sandboxed to `allow-scripts` ONLY. No `allow-same-origin`, so the
 * server's HTML has no access to this page's origin, cookies, or storage.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { IconPlugConnected, IconExternalLink } from "@tabler/icons-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  fetchMcpApp,
  type JsonSchema,
  type McpElicitationRequest,
  type McpElicitationResponse,
  type McpServerConfig,
  type ReviewInterrupt,
} from "@/lib/api";
import { createMcpAppHost } from "@/lib/mcpAppHost";

const LABEL = "text-[11px] font-bold uppercase tracking-[0.03em] text-muted-foreground";

interface Props {
  review: ReviewInterrupt;
  busy?: boolean;
  /** MCP servers on the active assistant, needed to read the paused tool's app. */
  servers: McpServerConfig[];
  /**
   * Arguments the paused tool was called with. An app is handed them over
   * `ui/notifications/tool-input` and sends them back with its answer, because
   * answering is a fresh call to the same tool (SEP-2322).
   */
  toolArguments?: Record<string, unknown>;
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

/* --------------------------- The server's own UI -------------------------- */

/**
 * The server's own HTML, rendered as an MCP App.
 *
 * Everything about the conversation with it lives in `lib/mcpAppHost.ts`, which
 * speaks SEP-1865 over JSON-RPC. This component owns the frame and its height,
 * which is all a React component should need to know about a foreign document.
 */
function AppFrame({
  request,
  toolName,
  toolArguments,
  html,
  busy,
  onAnswer,
}: {
  request: McpElicitationRequest;
  toolName: string;
  toolArguments: Record<string, unknown>;
  html: string;
  busy?: boolean;
  onAnswer: (response: McpElicitationResponse) => void;
}) {
  const ref = useRef<HTMLIFrameElement | null>(null);
  const [height, setHeight] = useState(300);

  useEffect(() => {
    const host = createMcpAppHost({
      toolName,
      toolArguments,
      request,
      onAnswer,
      // Clamped here rather than in the host: the ceiling is this card's
      // layout, and it is the same number the host advertises as maxHeight.
      onHeight: (h) => setHeight(Math.min(Math.max(h, 160), 640)),
    });
    const view = () => ref.current?.contentWindow ?? null;
    const onMessage = (event: MessageEvent) => host.handleMessage(event, view());
    window.addEventListener("message", onMessage);
    return () => {
      window.removeEventListener("message", onMessage);
      host.teardown(view(), "The pause was resolved.");
    };
  }, [request, toolName, toolArguments, onAnswer]);

  return (
    <div className="flex flex-col gap-2">
      <iframe
        ref={ref}
        title="MCP app"
        srcDoc={html}
        // Scripts only. Without allow-same-origin the frame is its own opaque
        // origin, so the server's HTML cannot touch this page or its storage.
        // See the deviation note in lib/mcpAppHost.ts for why there is no
        // sandbox proxy in front of it.
        sandbox="allow-scripts"
        className="w-full rounded-lg border border-border bg-background"
        style={{ height }}
      />
      {busy && <span className="text-[11px] text-muted-foreground">Sending to the server…</span>}
    </div>
  );
}

/* --------------------------------- Shell --------------------------------- */

export function McpElicitationCard({
  review,
  busy,
  servers,
  toolArguments,
  onApprove,
}: Props) {
  // Memoized so `answer` below keeps a stable identity across renders.
  const requests = useMemo(() => review.requests || [], [review]);
  const toolName = String(review.tool_name || "");
  const [app, setApp] = useState<{ html: string } | null>(null);
  // Stable identity, or the app host would be rebuilt on every render.
  const args = useMemo(() => toolArguments ?? {}, [toolArguments]);
  const [looking, setLooking] = useState(true);

  // Only the first request gets a custom UI: an MCP App is bound to the tool, so
  // it answers the tool's question, and a second one in the same round would have
  // nothing to render. In practice a round carries one.
  const first: McpElicitationRequest | undefined = requests[0];

  useEffect(() => {
    let live = true;
    if (!toolName || !servers.length) {
      setLooking(false);
      return;
    }
    void fetchMcpApp(servers, toolName).then((found) => {
      if (!live) return;
      setApp(found ? { html: found.html } : null);
      setLooking(false);
    });
    return () => {
      live = false;
    };
  }, [toolName, servers]);

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
  // When the server ships a UI it owns the presentation, including the prompt:
  // the app is handed `request.message` on init and renders it itself. Printing
  // it here too showed the same sentence twice. Held back while the app lookup
  // is still in flight, so it does not flash in and out on the way.
  const ownsPresentation = first.mode !== "url" && (looking || !!app);

  return (
    <div className="flex animate-in flex-col gap-2.5 rounded-xl border border-brand/40 bg-panel-2 p-3 duration-200 fade-in slide-in-from-bottom-1">
      <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-brand">
        <IconPlugConnected size={13} />
        {server?.label || "Connected system"} needs input
        <span className="ml-auto font-normal normal-case tracking-normal text-muted-foreground">
          {toolName}
        </span>
      </div>

      {first.message && !ownsPresentation && (
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
      ) : looking ? (
        <div className="h-6 text-[12px] text-muted-foreground">Loading the tool's interface…</div>
      ) : app ? (
        <AppFrame
          request={first}
          toolName={toolName}
          toolArguments={args}
          html={app.html}
          busy={busy}
          onAnswer={answer}
        />
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
