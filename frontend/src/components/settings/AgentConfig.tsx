/**
 * AGENT CONFIG (section 4). The system prompt comes from one place: the Context
 * Hub agent repo picked here, whose AGENTS.md the agent pulls fresh on every
 * question. Then the model this assistant runs on.
 *
 * The repo choice feeds the per-run context only and reloads from the assistant on
 * select. The model is different: it is PERSISTED onto the assistant (see
 * `editModel`), because a model chosen here has to survive picking another
 * assistant and coming back.
 */
import { IconArrowUpRight } from "@tabler/icons-react";
import { MODEL_CHOICES } from "@/lib/api";
import { Label } from "@/components/ui/label";
import { Combobox } from "@/components/ui/combobox";
import { LABEL_CLS } from "./types";


interface Props {
  agentRepo: string;
  model: string;
  agents: string[];
  onAgentRepo: (v: string) => void;
  onModel: (v: string) => void;
}

export function AgentConfig({ agentRepo, model, agents, onAgentRepo, onModel }: Props) {
  // Keep the assistant's saved repo selectable even if absent from the list.
  const extraAgent = agentRepo && !agents.includes(agentRepo) ? [agentRepo] : [];
  // Convenience link to the repo in LangSmith (best-effort; opens latest).
  const hubLink = agentRepo ? `https://smith.langchain.com/context/${agentRepo}` : "";

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
              title="Open in Context Hub"
              className="text-muted-foreground transition-colors hover:text-foreground"
            >
              <IconArrowUpRight className="h-3.5 w-3.5" stroke={2} />
            </a>
          )}
        </div>
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
