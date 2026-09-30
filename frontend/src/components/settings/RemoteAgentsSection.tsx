/**
 * REMOTE AGENTS - other agents, reached over A2A, that this assistant uses as subagents.
 *
 * The subagent counterpart of MCP servers: paste an agent's A2A agent card (or its
 * endpoint) and on the next message it is listed in the assistant's `task` tool
 * beside the built-in subagents, dispatchable from interpreter code with `task()`,
 * and startable in the background with `start_remote_task`, whose result wakes the
 * conversation when it lands. See custom_demo/runtime/remote_subagents.py.
 *
 * Persisted onto the assistant's `context.remote_agents`, the same way MCP servers
 * are, because an agent belongs to the assistant rather than to one conversation.
 *
 * "Test" reads the card through the deployment, which is how the runtime reads it,
 * so what it shows is what the model will be told the agent does.
 */
import { useState } from "react";
import { IconAlertTriangle, IconCheck, IconPlus, IconTrash } from "@tabler/icons-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { probeRemoteAgent, type RemoteAgentConfig, type RemoteAgentProbe } from "@/lib/api";
import { CollapseSection } from "./CollapseSection";
import { HINT_CLS, LABEL_CLS } from "./types";

interface Props {
  agents: RemoteAgentConfig[];
  onChange: (agents: RemoteAgentConfig[]) => void;
  defaultOpen?: boolean;
}

/**
 * The subagent name the model calls this agent by. Mirrors `parse_agents` in
 * custom_demo/runtime/remote_agents.py: slugged, with underscores.
 */
function remoteAgentId(label: string, taken: Set<string>): string {
  const base =
    label
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "") || "agent";
  let id = base;
  let n = 2;
  while (taken.has(id)) id = `${base}_${n++}`;
  return id;
}

function AgentRow({
  agent,
  result,
  testing,
  onEdit,
  onRemove,
  onTest,
}: {
  agent: RemoteAgentConfig;
  result?: RemoteAgentProbe;
  testing: boolean;
  onEdit: (patch: Partial<RemoteAgentConfig>) => void;
  onRemove: () => void;
  onTest: () => void;
}) {
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border bg-panel-2 p-2.5">
      <div className="flex items-center gap-2">
        <Switch
          size="sm"
          className="shrink-0"
          checked={agent.enabled !== false}
          onCheckedChange={(v) => onEdit({ enabled: !!v })}
          aria-label="Offer this agent as a subagent"
        />
        <Input
          value={agent.label}
          onChange={(e) => onEdit({ label: e.target.value })}
          placeholder="Name, e.g. Order Tracker"
          className="flex-1"
          autoComplete="off"
        />
        <button
          type="button"
          onClick={onRemove}
          title="Remove this agent"
          className="shrink-0 rounded-md p-1 text-muted-foreground hover:bg-panel hover:text-foreground"
        >
          <IconTrash size={14} />
        </button>
      </div>

      <div className="flex flex-col gap-1">
        <Label className={LABEL_CLS}>Agent card or A2A endpoint</Label>
        <Input
          value={agent.url}
          onChange={(e) => onEdit({ url: e.target.value })}
          placeholder="https://host/.well-known/agent-card.json?assistant_id=..."
          spellCheck={false}
          autoComplete="off"
          data-1p-ignore="true"
        />
      </div>

      <div className="flex flex-col gap-1">
        <Label className={LABEL_CLS}>
          API key <span className={HINT_CLS}>optional</span>
        </Label>
        <Input
          type="password"
          value={agent.token ?? ""}
          onChange={(e) => onEdit({ token: e.target.value })}
          placeholder="Sent as an x-api-key header"
          autoComplete="off"
          data-1p-ignore="true"
        />
      </div>

      <div className="flex items-center gap-2">
        <Button type="button" size="sm" variant="secondary" disabled={testing || !agent.url.trim()} onClick={onTest}>
          {testing ? "Reading card…" : "Test"}
        </Button>
        {agent.id && <span className="font-mono text-[11px] text-muted-foreground">{agent.id}</span>}
        {result && !result.ok && (
          <span className="inline-flex items-start gap-1 text-[11px] leading-snug text-muted-foreground">
            <IconAlertTriangle size={12} className="mt-px shrink-0" />
            {result.error}
          </span>
        )}
      </div>

      {result?.ok && (
        <div className="flex flex-col gap-0.5 border-t border-border pt-1.5">
          <span className="inline-flex items-center gap-1 text-[11px] text-foreground">
            <IconCheck size={12} className="text-brand" />
            {result.name}
          </span>
          <span className="text-[10.5px] leading-snug text-muted-foreground">{result.description}</span>
        </div>
      )}
    </div>
  );
}

export function RemoteAgentsSection({ agents, onChange, defaultOpen }: Props) {
  const [results, setResults] = useState<Record<number, RemoteAgentProbe>>({});
  const [testing, setTesting] = useState<number | null>(null);

  /**
   * Edit one row, keeping its id in step with its name until it has been tested.
   * After that the id is frozen: the model knows the agent by it, and renaming it
   * mid-conversation would point the model at a subagent that no longer exists.
   */
  const edit = (index: number, patch: Partial<RemoteAgentConfig>) =>
    onChange(
      agents.map((a, i) => {
        if (i !== index) return a;
        const next = { ...a, ...patch };
        if (patch.label !== undefined && !results[index]) {
          const taken = new Set(agents.filter((_, j) => j !== index).map((x) => x.id || ""));
          next.id = remoteAgentId(next.label, taken);
        }
        return next;
      }),
    );

  const test = async (index: number) => {
    setTesting(index);
    const agent = agents[index];
    const result = await probeRemoteAgent(agent).catch(
      (err: unknown): RemoteAgentProbe => ({
        ok: false,
        id: agent.id || "",
        error: err instanceof Error ? err.message : String(err),
      }),
    );
    setTesting(null);
    setResults((prev) => ({ ...prev, [index]: result }));
    // An unnamed agent takes its name from its card, so the row reads like the
    // subagent the model will see.
    if (result.ok && !agent.label.trim()) edit(index, { label: result.name });
  };

  const live = agents.filter((a) => a.enabled !== false && a.url.trim()).length;

  return (
    <CollapseSection title={`Remote agents (${live})`} defaultOpen={defaultOpen}>
      <div className="flex flex-col gap-2">
        <p className="m-0 text-[11px] leading-snug text-muted-foreground">
          Add another team's agent over A2A and it becomes one of this assistant's
          subagents on the next message. Long work can run in the background; its
          answer arrives in the chat by itself when it finishes.
        </p>

        {agents.map((agent, index) => (
          <AgentRow
            key={index}
            agent={agent}
            result={results[index]}
            testing={testing === index}
            onEdit={(patch) => edit(index, patch)}
            onRemove={() => onChange(agents.filter((_, i) => i !== index))}
            onTest={() => void test(index)}
          />
        ))}

        <Button
          type="button"
          size="sm"
          variant="outline"
          className="w-fit text-primary"
          onClick={() => onChange([...agents, { label: "", url: "", enabled: true }])}
        >
          <IconPlus size={15} className="mr-1" /> Add an agent
        </Button>
      </div>
    </CollapseSection>
  );
}
