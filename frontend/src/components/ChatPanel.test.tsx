// @vitest-environment jsdom
import { createRef, type ReactNode } from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ChatPanel, { type ChatPanelHandle, type ChatPanelProps, type TurnResult } from "./ChatPanel";
import type { ThreadMessage } from "@/lib/api";
import type { McpAppCardProps } from "./chat/McpAppCard";

const { runStream, ensureThread, getThreadState, savedThreadId, resetThread, mcpAppBindings, McpAppCard } = vi.hoisted(() => ({
  runStream: vi.fn(),
  ensureThread: vi.fn(),
  getThreadState: vi.fn(),
  savedThreadId: vi.fn(),
  resetThread: vi.fn(),
  mcpAppBindings: vi.fn(),
  McpAppCard: vi.fn<(props: McpAppCardProps) => ReactNode>(),
}));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  runStream,
  ensureThread,
  getThreadState,
  savedThreadId,
  resetThread,
}));
vi.mock("@/lib/mcpClients", () => ({ mcpAppBindings }));
vi.mock("@/components/chat/McpAppCard", () => ({ McpAppCard }));
vi.mock("streamdown", () => ({ Streamdown: ({ children }: { children: ReactNode }) => children }));
vi.mock("@/components/chat/ToolChip", () => ({ ToolChip: () => null }));
vi.mock("@/components/chat/ToolChipGroup", () => ({ ToolChipGroup: () => null }));
vi.mock("@/components/chat/ReviewCard", () => ({ ReviewCard: () => null }));
vi.mock("@/components/chat/FeedbackRow", () => ({ FeedbackRow: () => null }));
vi.mock("@/components/agents/prompt-input", () => ({ PromptInput: () => null }));
vi.mock("@/components/agents/loading-states/reasoning-text", () => ({ ReasoningText: () => null }));

interface Frame {
  event: string;
  data: string;
  namespace: string[];
}

function frame(event: string, data: unknown, namespace: string[] = []): Frame {
  return { event, data: JSON.stringify(data), namespace };
}

function message(data: ThreadMessage, namespace: string[] = []): Frame {
  return frame("messages/partial", [data], namespace);
}

function stream() {
  const queue: (Frame | null)[] = [];
  let wake: (() => void) | undefined;
  async function* events() {
    while (true) {
      if (!queue.length) await new Promise<void>((resolve) => { wake = resolve; });
      const next = queue.shift();
      if (!next) return;
      yield next;
    }
  }
  return {
    events,
    send(...frames: (Frame | null)[]) {
      queue.push(...frames);
      wake?.();
    },
  };
}

function panel(overrides: Partial<ChatPanelProps> = {}) {
  const handle = createRef<ChatPanelHandle>();
  const onActivity = vi.fn<NonNullable<ChatPanelProps["onActivity"]>>();
  const onWidget = vi.fn();
  const onArtifact = vi.fn();
  let props: ChatPanelProps = {
    handleRef: handle, assistantId: "assistant", hasAssistant: true,
    getRunContext: () => ({ ls_workspace: "workspace" }),
    onActivity, onWidget, onArtifact, ...overrides,
  };
  const view = render(<ChatPanel {...props} />);
  return {
    handle, onWidget, onArtifact,
    rerender(patch: Partial<ChatPanelProps>) {
      props = { ...props, ...patch };
      view.rerender(<ChatPanel {...props} />);
    },
    activity: () => onActivity.mock.calls.at(-1)![0],
    async start(resume?: unknown, onProgress?: (name: string) => void) {
      const source = stream();
      runStream.mockReturnValueOnce(source.events());
      let result!: Promise<TurnResult>;
      await act(async () => {
        result = resume === undefined
          ? handle.current!.ask("Analyze this", undefined, onProgress)
          : handle.current!.resumeWith(resume);
      });
      return {
        async send(...frames: Frame[]) {
          await act(async () => { source.send(...frames); });
        },
        async finish() {
          let value!: TurnResult;
          await act(async () => { source.send(null); value = await result; });
          return value;
        },
      };
    },
  };
}

beforeEach(() => {
  runStream.mockReset();
  ensureThread.mockReset().mockResolvedValue("thread");
  getThreadState.mockReset().mockResolvedValue({ values: { messages: [] } });
  savedThreadId.mockReset().mockReturnValue(null);
  resetThread.mockReset();
  mcpAppBindings.mockReset().mockResolvedValue({});
  McpAppCard.mockReset().mockReturnValue(null);
});
afterEach(cleanup);

describe("streamed tool activity", () => {
  it("updates real IDs in first-seen order, retains results and code, and freezes pending calls", async () => {
    const ui = panel();
    const progress = vi.fn();
    const run = await ui.start(undefined, progress);
    await run.send(message({ type: "ai", tool_calls: [{ name: "execute", args: { command: "partial" } }] }));
    expect(ui.activity().chips).toEqual([]);
    expect(progress).not.toHaveBeenCalled();
    await run.send(message({ type: "ai", tool_calls: [
      { id: "command", name: "execute", args: { command: "python first.py" } },
      { id: "pending", name: "read_file", args: { file_path: "/workspace/data/input.csv" } },
    ] }));
    const first = ui.activity().chips;
    await run.send(message({ type: "tool", tool_call_id: "missing", content: "ignored" }));
    await run.send(message({ type: "tool", tool_call_id: "command", content: "" }));
    await run.send(message({ type: "ai", tool_calls: [
      { id: "command", name: "execute", args: { command: "python final.py\nprint(2)" } },
    ] }));
    expect(ui.activity().chips.map((chip) => chip.id)).toEqual(["command", "pending"]);
    expect(ui.activity().chips[0]).toMatchObject({ name: "execute", arg: "python final.py", code: "python final.py\nprint(2)", codeLang: "bash", result: "" });
    expect(first[0]).toMatchObject({ arg: "python first.py", result: null });
    await run.send(message({ type: "ai", tool_calls: [
      { id: "command", name: "read_file", args: { file_path: "/workspace/data/other.csv" } },
    ] }));
    expect(ui.activity().chips[0]).toMatchObject({ name: "execute", arg: "/workspace/data/other.csv", code: "python final.py\nprint(2)", result: "" });
    expect(progress.mock.calls).toEqual([["execute"], ["read_file"]]);
    await run.finish();
    expect(ui.activity().running).toBe(false);
    expect(ui.activity().chips[0].stopped).toBeUndefined();
    expect(ui.activity().chips[1].stopped).toBe(true);
  });

  it("isolates namespace stores and preserves dispatch names, widgets, and artifact notifications", async () => {
    const ui = panel();
    const progress = vi.fn();
    const run = await ui.start(undefined, progress);
    const widget = { type: "kpi", title: "Revenue", value: "42" };
    const path = "/workspace/artifacts/report.html";
    await run.send(message({ type: "ai", tool_calls: [
      { id: "dispatch", name: "task", args: { subagent_type: "analyst", description: "Check the data" } },
      { id: "shared", name: "execute", args: { command: "main command" } },
      { id: "write", name: "write_file", args: { file_path: path, content: "<p>Report</p>" } },
      { id: "widget", name: "push_widget", args: { widget } },
    ] }));
    await run.send(message({ type: "ai", tool_calls: [
      { name: "execute", args: { command: "no id" } },
      { id: "shared", name: "execute", args: { command: "sub command" } },
      { id: "sub-widget", name: "push_widget", args: { widget: { ...widget, title: "Private" } } },
    ] }, ["tools:first"]));
    await run.send(frame("updates", { model: { messages: [{ type: "ai", tool_calls: [
      { id: "shared", name: "execute", args: { command: "other branch" } },
    ] }] } }, ["tools:first", "1"]));
    await run.send(message({ type: "tool", tool_call_id: "shared", content: "sub result" }, ["tools:first"]));
    await run.send(message({ type: "ai", tool_calls: [
      { id: "shared", name: "execute", args: { command: "sub final" } },
    ] }, ["tools:first"]));
    await run.send(message({ type: "ai", content: "Subagent conclusion" }, ["tools:first"]));
    const active = ui.activity();
    expect(active.chips.find((c) => c.id === "shared")).toMatchObject({ result: null, code: "main command" });
    expect(active.subagents).toHaveLength(2);
    expect(active.subagents[0]).toMatchObject({ label: "Analyst", invokedWith: "Check the data", done: false, text: "Subagent conclusion" });
    expect(active.subagents[0].chips).toHaveLength(2);
    expect(active.subagents[0].chips[0]).toEqual({ id: "shared", name: "execute", arg: "sub final", result: "sub result" });
    expect(active.subagents[1].chips[0]).toEqual({ id: "shared", name: "execute", arg: "other branch", result: null });
    expect(ui.onWidget).not.toHaveBeenCalled();
    await run.send(message({ type: "tool", name: "write_file", tool_call_id: "write", content: "written" }));
    await run.send(message({ type: "ai", content: "Main answer" }));
    const result = await run.finish();
    expect(result.answer).toBe("Main answer");
    expect(result.widgets).toEqual([widget]);
    expect(ui.onWidget.mock.calls).toEqual([[widget]]);
    expect(ui.onArtifact.mock.calls).toEqual([
      [{ path, content: "<p>Report</p>", streaming: true }],
      [{ path, content: "", streaming: false }],
    ]);
    expect(progress.mock.calls).toEqual([["task"], ["execute"], ["write_file"], ["push_widget"]]);
    expect(ui.activity().subagents.every((group) => group.done)).toBe(true);
    expect(ui.activity().subagents[0].chips[0].stopped).toBeUndefined();
    expect(ui.activity().subagents[0].chips[1].stopped).toBe(true);
    expect(ui.activity().subagents[1].chips[0].stopped).toBe(true);
  });

  it("keeps interrupted calls live and transfers only pending main chips on resume", async () => {
    const ui = panel();
    const run = await ui.start();
    await run.send(message({ type: "ai", tool_calls: [
      { id: "read", name: "read_file", args: { file_path: "/workspace/data/input.csv" } },
      { id: "review", name: "ask_user", args: { question: "Which option?" } },
    ] }));
    await run.send(message({ type: "tool", tool_call_id: "read", content: "data" }));
    await run.send(message({ type: "ai", tool_calls: [{ id: "sub", name: "execute", args: { command: "pending" } }] }, ["tools:paused"]));
    await run.send(frame("updates", { __interrupt__: [{ value: { kind: "user_question", question: "Which option?", options: ["A", "B"] } }] }));
    expect((await run.finish()).approval).toBe("Which option? Options: A; B.");
    expect(ui.activity().chips[1]).toMatchObject({ id: "review", result: null });
    expect(ui.activity().chips[1].stopped).toBeUndefined();
    expect(ui.activity().subagents[0].done).toBe(false);
    expect(ui.activity().subagents[0].chips[0].stopped).toBeUndefined();
    const paused = ui.activity().chips;
    const resumed = await ui.start({ answer: "A" });
    await resumed.send(message({ type: "tool", tool_call_id: "review", content: "A" }));
    expect(ui.activity().chips.map((chip) => chip.id)).toEqual(["read", "review"]);
    expect(ui.activity().chips[1]).toMatchObject({ result: "A", stopped: false });
    expect(paused[1].result).toBeNull();
    const options = runStream.mock.calls[1][0];
    expect(options.resume).toEqual({ answer: "A" });
    expect(options).not.toHaveProperty("messages");
    await resumed.finish();
    expect(ui.activity().chips[0]).toMatchObject({ id: "read", result: "data" });
    expect(ui.activity().chips[1].stopped).toBe(false);
  });

  it("finalizes the main chip and MCP app from the same result without accepting a subagent result", async () => {
    const servers = [{ id: "ops", label: "Operations", url: "https://ops.example/mcp" }];
    mcpAppBindings.mockResolvedValue({ ops_open_record: { resourceUri: "ui://ops/record" } });
    const ui = panel({ getRunContext: () => ({ mcp_servers: servers }) });
    const progress = vi.fn();
    const run = await ui.start(undefined, progress);
    const app = () => McpAppCard.mock.calls.at(-1)![0];
    await run.send(message({ type: "ai", tool_calls: [
      { id: "record", name: "ops_open_record", args: { query: "par" } },
    ] }));
    expect(app()).toMatchObject({ toolName: "ops_open_record", toolArguments: { query: "par" }, streaming: true, servers });
    await run.send(message({ type: "ai", tool_calls: [
      { id: "record", name: "ops_open_record", args: { query: "partial" } },
    ] }));
    expect(app().toolArguments).toEqual({ query: "partial" });
    await run.send(message({ type: "ai", tool_calls: [
      { id: "record", name: "ops_open_record", args: {} },
    ] }, ["tools:delegate"]));
    await run.send(message({ type: "tool", name: "ops_open_record", tool_call_id: "record", content: "subagent record" }, ["tools:delegate"]));
    expect(ui.activity().subagents[0].chips[0].result).toBe("subagent record");
    expect(ui.activity().chips[0].result).toBeNull();
    expect(app().streaming).toBe(true);
    expect(app().toolResult).toBeUndefined();
    const completed = {
      type: "tool", name: "ops_open_record", tool_call_id: "record",
      content: "The model-facing summary",
      artifact: { structured_content: { record_id: 42, status: "ready" } },
    };
    await run.send(message(completed));
    expect(ui.activity().chips).toHaveLength(1);
    expect(ui.activity().chips[0]).toMatchObject({ id: "record", result: "The model-facing summary" });
    expect(app()).toMatchObject({
      streaming: false,
      toolArguments: { query: "partial" },
      toolResult: { structuredContent: { record_id: 42, status: "ready" }, content: [] },
    });
    expect(progress.mock.calls).toEqual([["ops_open_record"]]);
    await run.finish();
    expect(ui.activity().chips[0].stopped).toBeUndefined();
    expect(app().streaming).toBe(false);
  });
});

const HISTORY = [
  { type: "human", content: "Open the saved record" },
  { type: "ai", content: "Opening it", tool_calls: [{ id: "saved-record", name: "ops_open_record", args: { id: 42 } }] },
  { type: "tool", tool_call_id: "saved-record", content: "Saved summary", artifact: { structured_content: { record_id: 42 } } },
  { type: "ai", content: "Saved answer" },
];

describe("persisted conversations", () => {
  it("restores answers and MCP apps as the assistant loads, resetting only on a deliberate switch", async () => {
    savedThreadId.mockReturnValue("saved-thread");
    getThreadState.mockResolvedValue({ values: { messages: HISTORY } });
    mcpAppBindings.mockImplementation(async (servers) => servers.length
      ? { ops_open_record: { resourceUri: "ui://ops/record" } }
      : {});
    const ui = panel({ assistantId: "", hasAssistant: false, resetKey: ":0" });
    await screen.findByText("Saved answer");
    expect(McpAppCard).not.toHaveBeenCalled();
    const servers = [{ id: "ops", label: "Operations", url: "https://ops.example/mcp" }];
    ui.rerender({ assistantId: "assistant", hasAssistant: true, resetKey: "assistant:0", getRunContext: () => ({ mcp_servers: servers }) });
    await waitFor(() => expect(McpAppCard).toHaveBeenCalled());
    expect(McpAppCard.mock.calls.at(-1)![0]).toMatchObject({
      toolName: "ops_open_record", streaming: false, toolArguments: { id: 42 },
      toolResult: { structuredContent: { record_id: 42 }, content: [] },
    });
    expect(getThreadState).toHaveBeenCalledWith("saved-thread");
    expect(screen.queryByText("Saved answer")).not.toBeNull();
    expect(resetThread).not.toHaveBeenCalled();
    expect(ui.activity()).toEqual({ chips: [], subagents: [], running: false });
    ui.rerender({ assistantId: "other", resetKey: "other:1" });
    expect(resetThread).toHaveBeenCalledOnce();
    expect(screen.queryByText("Saved answer")).toBeNull();
  });

  it("does not replace a new streamed turn with late restored history", async () => {
    savedThreadId.mockReturnValue("saved-thread");
    let restore!: (state: { values: { messages: typeof HISTORY } }) => void;
    getThreadState.mockReturnValue(new Promise((resolve) => { restore = resolve; }));
    const ui = panel({ resetKey: "assistant:0" });
    const run = await ui.start();
    await run.send(message({ type: "ai", tool_calls: [{ id: "current", name: "execute", args: { command: "current work" } }] }));
    await act(async () => { restore({ values: { messages: HISTORY } }); });
    expect(screen.queryByText("Saved answer")).toBeNull();
    expect(screen.queryByText("Analyze this")).not.toBeNull();
    expect(ui.activity().chips[0]).toMatchObject({ id: "current", result: null });
    await run.finish();
  });
});
