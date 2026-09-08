/**
 * AGENT CONFIG (section 4). System prompt as a [Prompt Hub | Context Hub | Prompt]
 * segmented toggle: the first two show a workspace-scoped picker, the third an
 * inline <Textarea>. Then the model this assistant runs on.
 *
 * The prompt edits feed the per-run context only and reload from the assistant on
 * select. The model is different: it is PERSISTED onto the assistant (see
 * `editModel`), because a model chosen here has to survive picking another
 * assistant and coming back.
 */
import { IconArrowUpRight } from "@tabler/icons-react";
import type { PromptMode } from "./types";
import { MODEL_CHOICES } from "@/lib/api";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Combobox } from "@/components/ui/combobox";
import { LABEL_CLS } from "./types";


interface Props {
  promptMode: PromptMode;
  promptName: string;
  agentRepo: string;
  systemPrompt: string;
  model: string;
  hubPrompts: string[];
  agents: string[];
  onPromptMode: (m: PromptMode) => void;
  onPromptName: (v: string) => void;
  onAgentRepo: (v: string) => void;
  onSystemPrompt: (v: string) => void;
  onModel: (v: string) => void;
}

export function AgentConfig({
  promptMode,
  promptName,
  agentRepo,
  systemPrompt,
  model,
  hubPrompts,
  agents,
  onPromptMode,
  onPromptName,
  onAgentRepo,
  onSystemPrompt,
  onModel,
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
              { value: "", label: "None - write a system prompt below" },
              ...hubPrompts.map((p) => ({ value: p, label: p })),
              ...extraPrompt.map((p) => ({ value: p, label: p })),
            ]}
            value={promptName || ""}
            onChange={(v) => onPromptName(v)}
            placeholder="None - write a system prompt below"
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
        <Label className={LABEL_CLS}>Model</Label>
        <Combobox
          options={[
            ...MODEL_CHOICES,
            // An id set outside this panel (env default, or the API) must show as
            // itself rather than silently read as the first option.
            ...(model && !MODEL_CHOICES.some((m) => m.value === model)
              ? [{ value: model, label: model }]
              : []),
          ]}
          value={model}
          onChange={onModel}
          placeholder="Claude Sonnet 5"
          searchPlaceholder="Filter models…"
          emptyText="No models configured."
        />
      </div>

    </div>
  );
}
