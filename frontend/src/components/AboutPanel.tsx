/**
 * "About this agent" — a scene-setting overlay a presenter can open at the top of a
 * demo: WHAT this assistant is, and HOW it is built.
 *
 * What it shows: this assistant in the setup agent's own words, the suggested demo flow,
 * and an architecture diagram. The first two come from the assistant's own metadata, so
 * they are correct per customer without anyone editing a string.
 *
 * Ported from the Super Group build (joel-langchain, 9115f8a). Two sections that came
 * with it have since been dropped as pitch rather than explanation: a "Built on
 * deepagents" list and a restatement of the quick-action prompts, which are already
 * visible under the composer. Its copy never came over at all - it named Super Group,
 * Betway, Spin, Kambi and Snowflake in ordinary prose, and hardcoding one customer's
 * systems into a shared panel shows a competitor's name in every other demo.
 *
 * That metadata already exists and is already per-customer: `build_demo_brief()` writes
 * `demo_brief` and `demo_flow` during setup, and the post-setup popup shows the same two
 * lists through the same components (see BriefLists). This panel is the reopenable way
 * back to them, which is the thing that was actually missing: the brief appeared once,
 * right after setup, and was then gone for good.
 *
 * Pure presentation. It renders metadata the header has already loaded, makes no
 * requests, and cannot change what the agent does.
 */
import type { ReactNode } from "react";
import { IconClipboardText, IconRoute } from "@tabler/icons-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { BulletList, NumberedList } from "@/components/settings/BriefLists";
import type { AssistantMetadata } from "@/lib/api";

export interface AboutPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  meta?: AssistantMetadata;
  displayName: string;
  logo?: ReactNode;
}

export function AboutPanel({ open, onOpenChange, meta, displayName, logo }: AboutPanelProps) {
  const industry = meta?.industry;
  const brief = meta?.demo_brief ?? [];
  const flow = meta?.demo_flow ?? [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[90vh] w-[min(940px,94vw)] max-w-none flex-col gap-0 overflow-hidden p-0 sm:max-w-none">
        <DialogHeader className="flex-row items-center gap-2.5 border-b border-border p-4 pr-12">
          {logo}
          <div>
            <DialogTitle className="font-heading text-lg font-bold tracking-tight">
              About {displayName}
            </DialogTitle>
            <DialogDescription className="text-xs text-muted-foreground">
              {industry
                ? `An internal AI assistant for ${industry}, built on deepagents.`
                : "An internal AI assistant built on deepagents."}
            </DialogDescription>
          </div>
        </DialogHeader>

        <div className="flex min-h-0 flex-col gap-5 overflow-y-auto p-5">
          {/* What this assistant is, in the setup agent's own words for THIS customer. */}
          {brief.length > 0 && (
            <section className="rounded-xl border border-border bg-panel p-4">
              <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold text-[var(--brand-primary)]">
                <IconClipboardText size={16} />
                About this assistant
              </h3>
              <BulletList items={brief} />
            </section>
          )}

          {flow.length > 0 && (
            <section className="rounded-xl border border-border bg-panel p-4">
              <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold text-[var(--brand-primary)]">
                <IconRoute size={16} />
                Suggested demo flow
              </h3>
              <NumberedList items={flow} />
            </section>
          )}

          <section className="flex flex-col gap-2">
            <h3 className="text-sm font-semibold text-[var(--brand-primary)]">
              How it fits together
            </h3>
            <div className="rounded-xl border border-border bg-panel p-3">
              <ArchitectureDiagram />
            </div>
          </section>

        </div>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Fixed palette for the diagram, NOT the assistant's brand colours.
 *
 * Two reasons. The diagram explains the platform, which is the same whoever the demo is
 * for, so it should look the same every time rather than inheriting whatever palette the
 * setup agent guessed from a logo. And the brand route was actually broken: the ported
 * version filled the harness and the tool chips with `var(--brand-tint)`, which
 * branding.ts sets to a PERCENTAGE (`12%`) for use inside color-mix, not a colour. An
 * invalid fill renders black, so the harness came out black with black-on-black tool
 * labels.
 *
 * Structure comes from the app's own theme tokens, which are already light/dark aware;
 * only the accent is fixed, mixed into a wash so it works on either background.
 */
const C = {
  accent: "#3b6fd4",
  accentText: "#ffffff",
  /** Card and chip faces: the plain panel colour, so text at currentColor always reads. */
  face: "var(--panel)",
  /** The harness box and the two bands: a faint accent wash over the panel. */
  wash: "color-mix(in srgb, #3b6fd4 10%, var(--panel))",
  bandWash: "color-mix(in srgb, #3b6fd4 6%, var(--panel))",
  stroke: "var(--border)",
} as const;

/**
 * A static architecture diagram: a question flows into the deepagents harness wrapping
 * the model (sandbox, memory, filesystem, skills, sub-agents, tools), which returns a
 * live dashboard and written answer, configured per assistant and traced in LangSmith.
 */
function ArchitectureDiagram() {
  return (
    <svg
      viewBox="0 0 940 576"
      className="h-auto w-full text-foreground"
      role="img"
      aria-label="Architecture: a question flows into the deepagents harness around the model, which returns a live dashboard and written answer; configured per assistant and traced in LangSmith."
    >
      <defs>
        <marker
          id="arrow"
          viewBox="0 0 10 10"
          refX="8"
          refY="5"
          markerWidth="7"
          markerHeight="7"
          orient="auto-start-reverse"
        >
          <path d="M0,0 L10,5 L0,10 z" fill={C.accent} />
        </marker>
      </defs>

      {/* Config band (top) */}
      <g>
        <rect
          x="20"
          y="10"
          width="900"
          height="46"
          rx="10"
          fill={C.bandWash}
          stroke={C.stroke}
        />
        <text x="470" y="33" textAnchor="middle" fontSize="13" fontWeight="700" fill="currentColor">
          Assistant configuration
        </text>
        <text x="470" y="48" textAnchor="middle" fontSize="11" fill="currentColor" opacity="0.7">
          one graph, many assistants · prompt · data · branding · enabled tools
        </text>
      </g>
      <line
        x1="470"
        y1="56"
        x2="470"
        y2="86"
        stroke={C.accent}
        strokeWidth="2"
        markerEnd="url(#arrow)"
      />

      {/* Input card */}
      <g>
        <rect x="16" y="250" width="150" height="86" rx="12" fill={C.face} stroke={C.stroke} />
        <text x="91" y="285" textAnchor="middle" fontSize="13" fontWeight="700" fill="currentColor">
          A question
        </text>
        <text x="91" y="305" textAnchor="middle" fontSize="11" fill="currentColor" opacity="0.7">
          plain language
        </text>
      </g>
      <line x1="166" y1="293" x2="214" y2="293" stroke={C.accent} strokeWidth="2" markerEnd="url(#arrow)" />

      {/* Harness box */}
      <g>
        <rect x="220" y="86" width="500" height="404" rx="16" fill={C.wash} stroke={C.stroke} strokeWidth="1.5" />
        <text x="240" y="114" fontSize="13" fontWeight="800" fill={C.accent}>
          deepagents harness
        </text>
        <text x="240" y="132" fontSize="11" fill="currentColor" opacity="0.7">
          the agent framework around the model
        </text>

        {/* Model core */}
        <rect x="345" y="146" width="250" height="52" rx="26" fill={C.accent} />
        <text x="470" y="172" textAnchor="middle" fontSize="14" fontWeight="800" fill={C.accentText}>
          Model (Claude)
        </text>
        <text x="470" y="189" textAnchor="middle" fontSize="10.5" fill={C.accentText} opacity="0.85">
          bring your own model
        </text>

        {/* Capability chips (2 columns) */}
        {[
          ["Sandbox", "runs code in a VM"],
          ["Long-term memory", "remembers context"],
          ["Virtual filesystem", "read / write files"],
          ["Sub-agents", "isolate context and parallelize work"],
          ["Skills", "reusable procedures"],
          ["Human-in-the-loop", "pause for approval"],
        ].map(([title, sub], i) => {
          const col = i % 2;
          const row = Math.floor(i / 2);
          const x = 240 + col * 235;
          const y = 220 + row * 56;
          return (
            <g key={title}>
              <rect x={x} y={y} width="225" height="46" rx="10" fill={C.face} stroke={C.stroke} />
              <text x={x + 14} y={y + 20} fontSize="12" fontWeight="700" fill="currentColor">
                {title}
              </text>
              <text x={x + 14} y={y + 36} fontSize="10.5" fill="currentColor" opacity="0.7">
                {sub}
              </text>
            </g>
          );
        })}

        {/* Tools sub-box */}
        <rect x="240" y="398" width="460" height="76" rx="12" fill={C.face} stroke={C.stroke} />
        <text x="254" y="420" fontSize="12" fontWeight="700" fill="currentColor">
          Tools
        </text>
        {["datasearch", "push_widget", "draft_email", "web_search"].map((t, i) => {
          const x = 254 + i * 111;
          return (
            <g key={t}>
              <rect x={x} y="432" width="102" height="30" rx="8" fill={C.face} stroke={C.stroke} />
              <text x={x + 51} y="451" textAnchor="middle" fontSize="11" fontWeight="600" fill="currentColor">
                {t}
              </text>
            </g>
          );
        })}
      </g>

      {/* Output card */}
      <line x1="720" y1="293" x2="770" y2="293" stroke={C.accent} strokeWidth="2" markerEnd="url(#arrow)" />
      <g>
        <rect x="772" y="240" width="156" height="106" rx="12" fill={C.face} stroke={C.stroke} />
        <text x="850" y="278" textAnchor="middle" fontSize="13" fontWeight="700" fill="currentColor">
          Live dashboard
        </text>
        <text x="850" y="298" textAnchor="middle" fontSize="13" fontWeight="700" fill="currentColor">
          + written answer
        </text>
        <text x="850" y="320" textAnchor="middle" fontSize="10.5" fill="currentColor" opacity="0.7">
          built as it streams
        </text>
      </g>

      {/* Observability band (bottom) */}
      <line x1="470" y1="490" x2="470" y2="518" stroke={C.accent} strokeWidth="2" markerEnd="url(#arrow)" />
      <g>
        <rect x="20" y="520" width="900" height="46" rx="10" fill={C.bandWash} stroke={C.stroke} />
        <text x="470" y="544" textAnchor="middle" fontSize="13" fontWeight="700" fill="currentColor">
          LangSmith observability
        </text>
        <text x="470" y="559" textAnchor="middle" fontSize="11" fill="currentColor" opacity="0.7">
          every step traced, evaluated, and improvable
        </text>
      </g>
    </svg>
  );
}
