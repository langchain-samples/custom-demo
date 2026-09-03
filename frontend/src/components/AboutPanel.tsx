/**
 * "About this agent" — a scene-setting overlay a presenter can open at the top of a
 * demo: WHAT this assistant is, and HOW it is built.
 *
 * Ported from the Super Group build (joel-langchain, 9115f8a), keeping the parts that
 * are true of every assistant: the deepagents framework story and the brand-aware
 * architecture diagram. What did NOT come over is that build's copy, which named Super
 * Group, Betway, Spin, Kambi and Snowflake in ordinary prose. Hardcoding one customer's
 * systems into a shared panel means every other demo shows a competitor's name, so the
 * customer-specific half is read from the assistant's own metadata instead.
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
  const actions = meta?.actions ?? [];
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

          {/* How it is built. True of every assistant, so it stays as written. */}
          <section className="rounded-xl border border-border bg-panel p-4">
            <h3 className="text-sm font-semibold text-[var(--brand-primary)]">
              Built on deepagents
            </h3>
            <ul className="mt-2 grid gap-2 text-sm leading-relaxed text-foreground/90 md:grid-cols-2">
              <li>
                <strong>Deepagents</strong> is LangChain&apos;s open-source framework for
                long-running agents.
              </li>
              <li>
                A strong agent is more than a model. Claude Code is roughly{" "}
                <strong>500,000 lines of code</strong> around the model.
              </li>
              <li>
                deepagents gives you that harness: planning, memory, filesystem,
                sub-agents, skills, and tool use.
              </li>
              <li>
                You bring the model and the domain, so you{" "}
                <strong>own your own intelligence</strong> instead of renting a black box.
              </li>
            </ul>
          </section>

          <section className="flex flex-col gap-2">
            <h3 className="text-sm font-semibold text-[var(--brand-primary)]">
              How it fits together
            </h3>
            <div className="rounded-xl border border-border bg-[var(--brand-tint)]/40 p-3">
              <ArchitectureDiagram />
            </div>
          </section>

          {actions.length > 0 && (
            <section className="flex flex-col gap-2">
              <h3 className="text-sm font-semibold text-[var(--brand-primary)]">
                {actions.length === 1
                  ? "The built-in prompt"
                  : `The ${actions.length} built-in prompts`}
              </h3>
              <p className="text-sm leading-relaxed text-foreground/90">
                The quick actions below the composer are ready-made prompts that show how
                the system works end to end:
              </p>
              <ul className="flex flex-col gap-1.5">
                {actions.map((a, i) => (
                  <li
                    key={i}
                    className="rounded-lg border border-border bg-panel px-3 py-2 text-sm"
                  >
                    <span className="font-semibold">{(a.label || "").split(":")[0]}</span>
                    <span className="text-muted-foreground">
                      {(a.label || "").includes(":")
                        ? ":" + (a.label || "").split(":").slice(1).join(":")
                        : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

/**
 * A static, brand-aware architecture diagram: a question flows into the deepagents
 * harness wrapping the model (planning, memory, filesystem, skills, sub-agents, tools),
 * which returns a live dashboard and written answer, configured per assistant and traced
 * in LangSmith. Uses the assistant's brand CSS variables so it matches the theme.
 */
function ArchitectureDiagram() {
  return (
    <svg
      viewBox="0 0 940 560"
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
          <path d="M0,0 L10,5 L0,10 z" fill="var(--brand-primary)" />
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
          fill="var(--brand-soft)"
          stroke="var(--brand-primary)"
          strokeOpacity="0.35"
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
        stroke="var(--brand-primary)"
        strokeWidth="2"
        markerEnd="url(#arrow)"
      />

      {/* Input card */}
      <g>
        <rect x="16" y="250" width="150" height="86" rx="12" fill="var(--brand-soft)" stroke="var(--brand-primary)" strokeOpacity="0.4" />
        <text x="91" y="285" textAnchor="middle" fontSize="13" fontWeight="700" fill="currentColor">
          A question
        </text>
        <text x="91" y="305" textAnchor="middle" fontSize="11" fill="currentColor" opacity="0.7">
          plain language
        </text>
      </g>
      <line x1="166" y1="293" x2="214" y2="293" stroke="var(--brand-primary)" strokeWidth="2" markerEnd="url(#arrow)" />

      {/* Harness box */}
      <g>
        <rect x="220" y="86" width="500" height="404" rx="16" fill="var(--brand-tint)" stroke="var(--brand-primary)" strokeWidth="1.5" />
        <text x="240" y="114" fontSize="13" fontWeight="800" fill="var(--brand-primary)">
          deepagents harness
        </text>
        <text x="240" y="132" fontSize="11" fill="currentColor" opacity="0.7">
          the agent framework around the model
        </text>

        {/* Model core */}
        <rect x="345" y="146" width="250" height="52" rx="26" fill="var(--brand-primary)" />
        <text x="470" y="172" textAnchor="middle" fontSize="14" fontWeight="800" fill="var(--brand-fg)">
          Model (Claude)
        </text>
        <text x="470" y="189" textAnchor="middle" fontSize="10.5" fill="var(--brand-fg)" opacity="0.85">
          bring your own model
        </text>

        {/* Capability chips (2 columns) */}
        {[
          ["Planning", "write_todos"],
          ["Long-term memory", "remembers context"],
          ["Virtual filesystem", "read / write files"],
          ["Sub-agents", "task() fan-out"],
          ["Skills", "reusable procedures"],
          ["Human-in-the-loop", "pause for approval"],
        ].map(([title, sub], i) => {
          const col = i % 2;
          const row = Math.floor(i / 2);
          const x = 240 + col * 235;
          const y = 220 + row * 56;
          return (
            <g key={title}>
              <rect x={x} y={y} width="215" height="46" rx="10" fill="var(--brand-soft)" stroke="var(--brand-primary)" strokeOpacity="0.3" />
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
        <rect x="240" y="398" width="460" height="76" rx="12" fill="var(--brand-soft)" stroke="var(--brand-primary)" strokeOpacity="0.3" />
        <text x="254" y="420" fontSize="12" fontWeight="700" fill="currentColor">
          Tools
        </text>
        {["datasearch", "push_widget", "draft_email", "web_search"].map((t, i) => {
          const x = 254 + i * 111;
          return (
            <g key={t}>
              <rect x={x} y="432" width="102" height="30" rx="8" fill="var(--brand-tint)" stroke="var(--brand-primary)" strokeOpacity="0.4" />
              <text x={x + 51} y="451" textAnchor="middle" fontSize="11" fontWeight="600" fill="currentColor">
                {t}
              </text>
            </g>
          );
        })}
      </g>

      {/* Output card */}
      <line x1="720" y1="293" x2="770" y2="293" stroke="var(--brand-primary)" strokeWidth="2" markerEnd="url(#arrow)" />
      <g>
        <rect x="772" y="240" width="156" height="106" rx="12" fill="var(--brand-soft)" stroke="var(--brand-primary)" strokeOpacity="0.4" />
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
      <line x1="470" y1="490" x2="470" y2="502" stroke="var(--brand-primary)" strokeWidth="2" markerEnd="url(#arrow)" />
      <g>
        <rect x="20" y="504" width="900" height="46" rx="10" fill="var(--brand-soft)" stroke="var(--brand-primary)" strokeOpacity="0.35" />
        <text x="470" y="528" textAnchor="middle" fontSize="13" fontWeight="700" fill="currentColor">
          LangSmith observability
        </text>
        <text x="470" y="543" textAnchor="middle" fontSize="11" fill="currentColor" opacity="0.7">
          every step traced, evaluated, and improvable
        </text>
      </g>
    </svg>
  );
}
