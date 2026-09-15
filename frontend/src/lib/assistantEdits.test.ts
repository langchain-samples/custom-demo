import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Assistant, UpdateAssistantInput } from "./api";
import { AssistantEdits } from "./assistantEdits";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function harness() {
  const records = new Map<string, Assistant>([
    ["a", { assistant_id: "a", graph_id: "dashboard_agent", context: { model: "old", agent_repo: "a-agent", extra: "keep" }, metadata: { voice: { voice_name: "old", extra: "keep" } } }],
    ["b", { assistant_id: "b", graph_id: "dashboard_agent", context: { agent_repo: "b-agent" } }],
  ]);
  const save = vi.fn<(id: string, patch: UpdateAssistantInput) => Promise<Assistant>>();
  save.mockImplementation(async (id, patch) => ({ ...records.get(id)!, ...patch }));
  const edits = new AssistantEdits({
    get: (id) => records.get(id),
    save,
    saved: (assistant) => { records.set(assistant.assistant_id, assistant); },
  });
  return { records, save, edits };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("assistant edit ownership", () => {
  it("serializes replacement-object writes and merges against the acknowledged assistant", async () => {
    const { edits, records, save } = harness();
    const first = deferred<Assistant>();
    save.mockImplementationOnce(() => first.promise);
    edits.schedule("a", "model", (a) => ({ context: { ...a.context, model: "new" } }), 600);
    edits.schedule("a", "tools", (a) => ({ context: { ...a.context, enabled_tools: [] } }), 600);
    await vi.advanceTimersByTimeAsync(600);
    expect(save).toHaveBeenCalledTimes(1);
    first.resolve({ ...records.get("a")!, ...save.mock.calls[0][1] });
    await vi.advanceTimersByTimeAsync(0);
    expect(save).toHaveBeenNthCalledWith(2, "a", {
      context: { model: "new", agent_repo: "a-agent", extra: "keep", enabled_tools: [] },
    });
  });

  it("debounces each field group without cancelling another assistant's edits", async () => {
    const { edits, save } = harness();
    edits.schedule("a", "tools", (a) => ({ context: { ...a.context, enabled_tools: ["old"] } }), 600);
    edits.schedule("a", "tools", (a) => ({ context: { ...a.context, enabled_tools: [] } }), 600);
    edits.schedule("b", "tools", (a) => ({ context: { ...a.context, enabled_tools: ["web_search"] } }), 600);
    edits.schedule("a", "mcp", (a) => ({ context: { ...a.context, mcp_servers: [] } }), 800);
    await vi.advanceTimersByTimeAsync(599);
    expect(save).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(save.mock.calls.map(([id]) => id)).toEqual(["a", "b"]);
    expect(save.mock.calls[0][1].context?.enabled_tools).toEqual([]);
    await vi.advanceTimersByTimeAsync(200);
    expect(save.mock.calls[2]).toEqual(["a", {
      context: { model: "old", agent_repo: "a-agent", extra: "keep", enabled_tools: [], mcp_servers: [] },
    }]);
  });

  it("does not let a failed request poison later edits or pretend it was saved", async () => {
    const { edits, save, records } = harness();
    save.mockRejectedValueOnce(new Error("offline"));
    edits.schedule("a", "model", (a) => ({ context: { ...a.context, model: "failed" } }), 600);
    edits.schedule("a", "tools", (a) => ({ context: { ...a.context, enabled_tools: [] } }), 600);
    await vi.advanceTimersByTimeAsync(600);
    expect(save).toHaveBeenCalledTimes(2);
    expect(records.get("a")?.context).toEqual({ model: "old", agent_repo: "a-agent", extra: "keep", enabled_tools: [] });
  });

  it("disposes pending timers without performing requests", async () => {
    const { edits, save } = harness();
    edits.schedule("a", "model", () => ({ context: {} }), 600);
    edits.dispose();
    await vi.runAllTimersAsync();
    expect(save).not.toHaveBeenCalled();
  });
});
