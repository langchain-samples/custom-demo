/**
 * MCP SERVERS - the remote tool servers this assistant connects to.
 *
 * Unlike the tool catalogue, which is a fixed menu the backend declares, an MCP
 * server is a URL the presenter pastes in: point the assistant at a customer's
 * own system (or at a laptop behind an ngrok tunnel) and its tools show up on the
 * next turn, with no code change and no redeploy.
 *
 * Persisted onto the assistant's `context.mcp_servers`, the same way the tool
 * selection is, because a connection belongs to the assistant rather than to one
 * conversation.
 *
 * "Test" is not decoration. A browser cannot speak MCP, so the deployment
 * connects on our behalf and reports the tool list back; that round trip is the
 * only way to find out whether a tunnel is up, a token is right, and the tools
 * are named what you expect, before a live demo depends on it.
 */
import { useState } from "react";
import {
  IconAlertTriangle,
  IconCheck,
  IconPlus,
  IconTrash,
  IconLayoutGrid,
} from "@tabler/icons-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { probeMcpServers, type McpProbeResult, type McpServerConfig } from "@/lib/api";
import { CollapseSection } from "./CollapseSection";
import { HINT_CLS, LABEL_CLS } from "./types";

interface Props {
  servers: McpServerConfig[];
  onChange: (servers: McpServerConfig[]) => void;
  defaultOpen?: boolean;
}

/**
 * A stable id from the label, used to namespace the server's tools as
 * `{id}_{tool}`. Derived once at creation and then left alone: changing it later
 * renames every tool, which invalidates the model's memory of them mid-demo.
 */
function idFor(label: string, taken: Set<string>): string {
  const base = label.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "") || "mcp";
  let id = base;
  let n = 2;
  while (taken.has(id)) id = `${base}_${n++}`;
  return id;
}

function ServerRow({
  server,
  result,
  testing,
  onEdit,
  onRemove,
  onTest,
}: {
  server: McpServerConfig;
  result?: McpProbeResult;
  testing: boolean;
  onEdit: (patch: Partial<McpServerConfig>) => void;
  onRemove: () => void;
  onTest: () => void;
}) {
  const enabled = server.enabled !== false;

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border bg-panel-2 p-2.5">
      <div className="flex items-center gap-2">
        <Switch
          className="shrink-0"
          checked={enabled}
          onCheckedChange={(v) => onEdit({ enabled: !!v })}
          aria-label="Connect this server"
        />
        <Input
          value={server.label}
          onChange={(e) => onEdit({ label: e.target.value })}
          placeholder="Name, e.g. Fieldlink"
          className="h-7 flex-1 text-[12.5px]"
          autoComplete="off"
        />
        <button
          type="button"
          onClick={onRemove}
          title="Remove this server"
          className="shrink-0 rounded-md p-1 text-muted-foreground hover:bg-panel hover:text-foreground"
        >
          <IconTrash size={14} />
        </button>
      </div>

      <div className="flex flex-col gap-1">
        <Label className={LABEL_CLS}>Connection string</Label>
        <Input
          value={server.url}
          onChange={(e) => onEdit({ url: e.target.value })}
          placeholder="https://your-tunnel.ngrok.app/mcp"
          className="h-7 font-mono text-[11.5px]"
          spellCheck={false}
          autoComplete="off"
        />
      </div>

      <div className="flex flex-col gap-1">
        <Label className={LABEL_CLS}>
          Bearer token <span className={HINT_CLS}>optional</span>
        </Label>
        <Input
          type="password"
          value={server.token ?? ""}
          onChange={(e) => onEdit({ token: e.target.value })}
          placeholder="Sent as an Authorization header"
          className="h-7 font-mono text-[11.5px]"
          autoComplete="off"
        />
      </div>

      <div className="flex items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          className="h-6 px-2 text-[11px]"
          disabled={testing || !server.url.trim()}
          onClick={onTest}
        >
          {testing ? "Connecting…" : "Test"}
        </Button>
        {result?.ok && (
          <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
            <IconCheck size={12} className="text-brand" />
            {result.tools.length} tool{result.tools.length === 1 ? "" : "s"}
          </span>
        )}
        {result && !result.ok && (
          <span className="inline-flex items-start gap-1 text-[11px] leading-snug text-muted-foreground">
            <IconAlertTriangle size={12} className="mt-px shrink-0" />
            {result.error || "could not connect"}
          </span>
        )}
      </div>

      {result?.ok && result.tools.length > 0 && (
        <div className="flex flex-col gap-1 border-t border-border pt-1.5">
          {result.tools.map((tool) => (
            <div key={tool.name} className="flex items-start gap-1.5">
              <span className="font-mono text-[11px] text-foreground">{tool.name}</span>
              {tool.app && (
                <span
                  title={`Ships its own UI: ${tool.app}`}
                  className="inline-flex shrink-0 items-center gap-0.5 rounded border border-brand/40 px-1 text-[9.5px] uppercase tracking-wide text-brand"
                >
                  <IconLayoutGrid size={9} /> app
                </span>
              )}
              <span className="min-w-0 flex-1 truncate text-[10.5px] text-muted-foreground">
                {tool.description}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function McpSection({ servers, onChange, defaultOpen }: Props) {
  const [results, setResults] = useState<Record<string, McpProbeResult>>({});
  const [testing, setTesting] = useState<string | null>(null);

  const edit = (index: number, patch: Partial<McpServerConfig>) =>
    onChange(servers.map((s, i) => (i === index ? { ...s, ...patch } : s)));

  const add = () => {
    const taken = new Set(servers.map((s) => s.id || ""));
    onChange([...servers, { id: idFor("mcp", taken), label: "", url: "", enabled: true }]);
  };

  const test = async (index: number) => {
    const server = servers[index];
    // Give an unnamed server its id now: the probe reports tools under the
    // prefix, and they should read the same here as they will in chat.
    const taken = new Set(servers.filter((_, i) => i !== index).map((s) => s.id || ""));
    const id = server.id || idFor(server.label || "mcp", taken);
    if (id !== server.id) edit(index, { id });

    setTesting(id);
    const [result] = await probeMcpServers([{ ...server, id, enabled: true }]);
    setTesting(null);
    if (result) setResults((prev) => ({ ...prev, [id]: result }));
  };

  const live = servers.filter((s) => s.enabled !== false && s.url.trim()).length;

  return (
    <CollapseSection title={`MCP servers (${live})`} defaultOpen={defaultOpen}>
      <div className="flex flex-col gap-2">
        <p className="m-0 text-[11px] leading-snug text-muted-foreground">
          Connect the assistant to a remote MCP server and its tools appear on the
          next message. A server on your own machine needs a public URL, such as an
          ngrok tunnel.
        </p>

        {servers.map((server, index) => (
          <ServerRow
            key={server.id || index}
            server={server}
            result={server.id ? results[server.id] : undefined}
            testing={testing !== null && testing === server.id}
            onEdit={(patch) => edit(index, patch)}
            onRemove={() => onChange(servers.filter((_, i) => i !== index))}
            onTest={() => void test(index)}
          />
        ))}

        <Button
          size="sm"
          variant="outline"
          className="h-7 w-fit gap-1 text-[11.5px]"
          onClick={add}
        >
          <IconPlus size={13} /> Add a server
        </Button>
      </div>
    </CollapseSection>
  );
}
