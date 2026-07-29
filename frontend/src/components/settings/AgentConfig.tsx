/**
 * AGENT CONFIG (section 4). System prompt as a [Prompt Hub | Prompt] segmented
 * toggle: Prompt Hub shows a workspace-scoped <Select> (first option
 * "None — write a system prompt below"); Prompt shows an inline <Textarea>.
 * Then a "Withheld data" input (context.data_gap) and a collapsed advanced
 * "Synthetic data prompt" textarea (context.data_prompt). No model selector.
 *
 * These edits feed the per-run context only — they are NOT saved back onto the
 * assistant (matching the SPA); they reload from the assistant's context on
 * select.
 */
import { IconArrowUpRight } from "@tabler/icons-react";
import type { PromptMode } from "./types";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Combobox } from "@/components/ui/combobox";
import { CollapseSection } from "./CollapseSection";
import { LABEL_CLS, HINT_CLS } from "./types";


interface Props {
  promptMode: PromptMode;
  promptName: string;
  agentRepo: string;
  systemPrompt: string;
  dataGap: string;
  dataPrompt: string;
  hubPrompts: string[];
  agents: string[];
  onPromptMode: (m: PromptMode) => void;
  onPromptName: (v: string) => void;
  onAgentRepo: (v: string) => void;
  onSystemPrompt: (v: string) => void;
  onDataGap: (v: string) => void;
  onDataPrompt: (v: string) => void;
}

export function AgentConfig({
  promptMode,
  promptName,
  agentRepo,
  systemPrompt,
  dataGap,
  dataPrompt,
  hubPrompts,
  agents,
  onPromptMode,
  onPromptName,
  onAgentRepo,
  onSystemPrompt,
  onDataGap,
  onDataPrompt,
}: Props) {
  // Keep the assistant's saved handle selectable even if absent from the list.
  const extraPrompt = promptName && !hubPrompts.includes(promptName) ? [promptName] : [];
  const extraAgent = agentRepo && !agents.includes(agentRepo) ? [agentRepo] : [];
  // Convenience link to the active source in LangSmith (best-effort; opens latest).
  const hubLink =
    promptMode === "prompt_hub" && promptName
      ? `https://smith.langchain.com/prompts/${promptName}`
      : promptMode === "context_hub" && agentRepo
        ? `https://smith.langchain.com/context/${agentRepo}`
        : "";

  return (
    <div className="flex flex-col gap-3.5">
      <div className={LABEL_CLS + " border-t border-border pt-3"}>Agent config</div>

      <div className="flex flex-col gap-1.5">
        <div className="flex items-center justify-between">
          <Label className={LABEL_CLS}>System prompt</Label>
          {hubLink && (
            <a
              href={hubLink}
              target="_blank"
              rel="noreferrer"
              title={promptMode === "context_hub" ? "Open in Context Hub" : "Open in Prompt Hub"}
              className="text-muted-foreground transition-colors hover:text-foreground"
            >
              <IconArrowUpRight className="h-3.5 w-3.5" stroke={2} />
            </a>
          )}
        </div>
        <Tabs value={promptMode} onValueChange={(v) => onPromptMode(v as PromptMode)}>
          <TabsList className="w-full">
            <TabsTrigger value="prompt_hub">Prompt Hub</TabsTrigger>
            <TabsTrigger value="context_hub">Context Hub</TabsTrigger>
            <TabsTrigger value="inline">Prompt</TabsTrigger>
          </TabsList>
        </Tabs>

        {promptMode === "prompt_hub" ? (
          <Combobox
            options={[
              { value: "", label: "None — write a system prompt below" },
              ...hubPrompts.map((p) => ({ value: p, label: p })),
              ...extraPrompt.map((p) => ({ value: p, label: p })),
            ]}
            value={promptName || ""}
            onChange={(v) => onPromptName(v)}
            placeholder="None — write a system prompt below"
            searchPlaceholder="Filter prompts…"
            emptyText="No prompts in this workspace."
          />
        ) : promptMode === "context_hub" ? (
          <Combobox
            options={[
              ...agents.map((a) => ({ value: a, label: a })),
              ...extraAgent.map((a) => ({ value: a, label: a })),
            ]}
            value={agentRepo || ""}
            onChange={(v) => onAgentRepo(v)}
            placeholder="Select an agent (its AGENTS.md is the prompt)…"
            searchPlaceholder="Filter agents…"
            emptyText="No agent repos in this workspace."
          />
        ) : (
          <Textarea
            placeholder="Write the system prompt"
            value={systemPrompt}
            onChange={(e) => onSystemPrompt(e.target.value)}
          />
        )}
      </div>

      <div className="flex flex-col gap-1.5">
        <Label className={LABEL_CLS}>
          Withheld data{" "}
          <span className={HINT_CLS}>(the "gap" in our data, to test hallucination)</span>
        </Label>
        <Input
          placeholder="e.g. conversion rate by traffic source"
          value={dataGap}
          onChange={(e) => onDataGap(e.target.value)}
          autoComplete="off"
          data-1p-ignore="true"
        />
      </div>

      <CollapseSection title="Synthetic data prompt">
        <Textarea
          placeholder="Advanced: overrides the auto-built customer data prompt"
          value={dataPrompt}
          onChange={(e) => onDataPrompt(e.target.value)}
        />
      </CollapseSection>
    </div>
  );
}
