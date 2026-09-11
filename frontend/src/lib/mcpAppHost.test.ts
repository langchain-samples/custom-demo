// @vitest-environment jsdom
/**
 * The host half of MCP Apps, checked against the message names SEP-1865 fixes.
 *
 * These names are a contract with software we did not write: a third-party app
 * rendered here, and our own apps rendered in someone else's host. A rename on
 * either side does not fail loudly, it just leaves a blank iframe and a run
 * paused forever, so the wire form is pinned here rather than inferred from the
 * one app we happen to ship.
 *
 * The View is a plain object rather than a real iframe: `handleMessage` matches
 * on `event.source`, and jsdom cannot run a srcdoc anyway. The app's own half of
 * this conversation is exercised for real in
 * `custom_demo/tests/signature_app_test.js`.
 */
import { describe, expect, it, vi } from "vitest";
import { createMcpAppHost, PROTOCOL_VERSION, type McpToolResult } from "./mcpAppHost";

/** What `sign_document` returns: the label the pad draws itself from. */
const RESULT = {
  structuredContent: {
    account_id: "MW-10241",
    household: "Whitfield Family Trust",
    document: "IPS amendment",
  },
};

/** A stand-in for the iframe's contentWindow that records what it is sent. */
function view() {
  const sent: Record<string, unknown>[] = [];
  const win = { postMessage: (m: Record<string, unknown>) => void sent.push(m) };
  return { win: win as unknown as Window, sent };
}

/** Build a host plus its View, wired the way the card wires them. */
function host(
  overrides: {
    onToolCall?: (name: string, args: Record<string, unknown>) => Promise<McpToolResult>;
    onReadResource?: (uri: string) => Promise<unknown[]>;
    onMessage?: (text: string) => void;
    onModelContext?: (c: Record<string, unknown>) => void;
    toolInputSchema?: Record<string, unknown>;
  } = {},
) {
  const target = view();
  const onToolCall = vi.fn(overrides.onToolCall ?? (async () => ({})));
  const onHeight = vi.fn();
  const bridge = createMcpAppHost({
    toolName: "meridian_sign_document",
    toolArguments: { account_id: "MW-10241", document: "IPS amendment" },
    toolResult: RESULT,
    toolInputSchema: overrides.toolInputSchema,
    onToolCall,
    onHeight,
    onReadResource: overrides.onReadResource,
    onMessage: overrides.onMessage,
    onModelContext: overrides.onModelContext,
  });
  /** Deliver one JSON-RPC message as if the View had posted it. */
  const from = (msg: Record<string, unknown>, source: unknown = target.win) =>
    bridge.handleMessage({ data: msg, source } as MessageEvent, target.win);
  return { bridge, target, onToolCall, onHeight, from };
}

/** The first message the host sent with this method. */
const sent = (msgs: Record<string, unknown>[], method: string) =>
  msgs.find((m) => m.method === method);

/** The reply to a given JSON-RPC id. */
const reply = (msgs: Record<string, unknown>[], id: number) =>
  msgs.find((m) => m.id === id && m.method === undefined);

describe("the handshake", () => {
  it("answers ui/initialize with the protocol version and the host's context", () => {
    const { target, from } = host();
    from({ jsonrpc: "2.0", id: 1, method: "ui/initialize", params: {} });

    const result = reply(target.sent, 1)?.result as Record<string, unknown>;
    expect(result).toBeTruthy();
    const ctx = result.hostContext as Record<string, unknown>;
    expect(result.protocolVersion).toBe(PROTOCOL_VERSION);
    // A COMPLETE `Tool`. `inputSchema` is required by that type and the official
    // app SDK validates the initialize result against it, so leaving it out is
    // not a cautious partial answer: Excalidraw's app rejected the handshake
    // with `path: ["hostContext","toolInfo","tool","inputSchema"]`.
    const tool = (ctx.toolInfo as { tool: Record<string, unknown> }).tool;
    expect(tool.name).toBe("meridian_sign_document");
    expect(tool.inputSchema).toEqual({ type: "object" });
    expect(ctx.displayMode).toBe("inline");
    // Flexible height, which is the mode that pairs with size-changed below.
    expect(ctx.containerDimensions).toEqual({ maxHeight: 640 });
  });

  it("hands over the call once the View confirms initialization", () => {
    const { target, from } = host();
    from({ jsonrpc: "2.0", method: "ui/notifications/initialized", params: {} });

    const input = sent(target.sent, "ui/notifications/tool-input");
    const result = sent(target.sent, "ui/notifications/tool-result");
    expect(input).toBeTruthy();
    expect((input!.params as { arguments: unknown }).arguments).toEqual({
      account_id: "MW-10241",
      document: "IPS amendment",
    });
    // Input first, then result. The spec requires that order, and an app that
    // renders from arguments while waiting would otherwise see them late.
    expect(target.sent.indexOf(input!)).toBeLessThan(target.sent.indexOf(result!));

    const params = result?.params as Record<string, unknown>;
    expect(params.structuredContent).toEqual(RESULT.structuredContent);
  });

  it("passes the server's real schema through when there is one", () => {
    const schema = { type: "object", properties: { elements: { type: "string" } } };
    const { target, from } = host({ toolInputSchema: schema });
    from({ jsonrpc: "2.0", id: 1, method: "ui/initialize", params: {} });
    const got = reply(target.sent, 1)?.result as Record<string, unknown> | undefined;
    const ctx = got?.hostContext as Record<string, unknown>;
    expect((ctx.toolInfo as { tool: { inputSchema: unknown } }).tool.inputSchema).toEqual(schema);
  });

  it("ignores anything that did not come from its own View", () => {
    const { target, from } = host();
    from({ jsonrpc: "2.0", id: 1, method: "ui/initialize", params: {} }, { other: true });
    expect(target.sent).toHaveLength(0);
  });

  it("ignores traffic that is not JSON-RPC", () => {
    const { target, from } = host();
    from({ type: "mcp-app:ready" });
    expect(target.sent).toHaveLength(0);
  });
});

describe("proxying what the app calls", () => {
  it("forwards a tools/call and replies with the result", async () => {
    const onToolCall = vi.fn(async () => ({ structuredContent: { reference: "MW-DOC-1" } }));
    const { target, from } = host({ onToolCall });
    from({
      jsonrpc: "2.0",
      id: 7,
      method: "tools/call",
      params: { name: "meridian_submit_signature", arguments: { signed_by: "Grace" } },
    });

    expect(onToolCall).toHaveBeenCalledWith("meridian_submit_signature", { signed_by: "Grace" });
    await vi.waitFor(() => {
      const got = reply(target.sent, 7)?.result as Record<string, unknown> | undefined;
      expect(got?.structuredContent).toEqual({ reference: "MW-DOC-1" });
    });
  });

  it("hands a refusal back as the reason, not as silence", async () => {
    const onToolCall = vi.fn(async () => {
      throw new Error("meridian_list_accounts is not open to apps");
    });
    const { target, from } = host({ onToolCall });
    from({
      jsonrpc: "2.0",
      id: 8,
      method: "tools/call",
      params: { name: "meridian_list_accounts", arguments: {} },
    });

    // A dropped request is a button that hangs, which is worse than a refusal.
    await vi.waitFor(() => {
      const e = reply(target.sent, 8)?.error as { message: string } | undefined;
      expect(e?.message).toMatch(/not open to apps/);
    });
  });

  it("refuses a call when no proxy is wired", () => {
    const { target, from } = host({ onToolCall: undefined });
    // The default stub in `host` is a proxy, so build one without it.
    const bare = createMcpAppHost({
      toolName: "t",
      toolArguments: {},
      toolResult: {},
      onHeight: () => {},
    });
    bare.handleMessage(
      { data: { jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "x" } }, source: target.win } as MessageEvent,
      target.win,
    );
    expect(reply(target.sent, 1)?.error).toBeTruthy();
    void from;
  });

  it("refuses a call with no name", () => {
    const { target, from } = host();
    from({ jsonrpc: "2.0", id: 9, method: "tools/call", params: {} });
    expect(reply(target.sent, 9)?.error).toBeTruthy();
  });
});

describe("the rest of the surface", () => {
  it("resizes the frame to the height the View reports", () => {
    const { onHeight, from } = host();
    from({
      jsonrpc: "2.0",
      method: "ui/notifications/size-changed",
      params: { width: 400, height: 288 },
    });
    expect(onHeight).toHaveBeenCalledWith(288);
  });

  it("opens an http link but refuses any other scheme", () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    const { target, from } = host();
    from({ jsonrpc: "2.0", id: 1, method: "ui/open-link", params: { url: "https://example.com" } });
    expect(open).toHaveBeenCalled();

    open.mockClear();
    // javascript: would run in THIS page, which is the whole point of the sandbox.
    from({ jsonrpc: "2.0", id: 2, method: "ui/open-link", params: { url: "javascript:alert(1)" } });
    expect(open).not.toHaveBeenCalled();
    expect(reply(target.sent, 2)?.error).toBeTruthy();
    open.mockRestore();
  });

  it("returns the display mode it actually granted", () => {
    const { target, from } = host();
    from({
      jsonrpc: "2.0",
      id: 4,
      method: "ui/request-display-mode",
      params: { mode: "fullscreen" },
    });
    // The card has room for inline only, and the spec wants the resulting mode
    // returned whether or not it changed.
    expect(reply(target.sent, 4)?.result).toEqual({ mode: "inline" });
  });

  it("answers an unsupported method rather than dropping it", () => {
    const { target, from } = host();
    from({ jsonrpc: "2.0", id: 5, method: "ui/nonsense", params: {} });
    expect(reply(target.sent, 5)?.error).toBeTruthy();
  });

  it("reads a resource for the View and replies with its contents", async () => {
    const contents = [{ uri: "tips://what-are-apps", text: "{}" }];
    const onReadResource = vi.fn(async () => contents as unknown[]);
    const { target, from } = host({ onReadResource });
    from({ jsonrpc: "2.0", id: 9, method: "resources/read", params: { uri: "tips://what-are-apps" } });

    expect(onReadResource).toHaveBeenCalledWith("tips://what-are-apps");
    // The read is async, so the reply lands a microtask later.
    await vi.waitFor(() => expect(reply(target.sent, 9)?.result).toEqual({ contents }));
  });

  it("tells the View why a failed read failed, rather than hanging it", async () => {
    const onReadResource = vi.fn(async () => {
      throw new Error("that server refused the read");
    });
    const { target, from } = host({ onReadResource });
    from({ jsonrpc: "2.0", id: 10, method: "resources/read", params: { uri: "tips://x" } });
    await vi.waitFor(() => {
      const e = reply(target.sent, 10)?.error as { message: string } | undefined;
      expect(e?.message).toMatch(/refused the read/);
    });
  });

  it("advertises serverResources only when a reader is wired", () => {
    const withReader = host({ onReadResource: async () => [] });
    withReader.from({ jsonrpc: "2.0", id: 1, method: "ui/initialize", params: {} });
    const got = reply(withReader.target.sent, 1)?.result as Record<string, unknown> | undefined;
    const caps = got?.hostCapabilities as Record<string, unknown> | undefined;
    expect(caps?.serverResources).toBeTruthy();

    // Claiming a capability the host then refuses is worse than never claiming it.
    const without = host();
    without.from({ jsonrpc: "2.0", id: 1, method: "ui/initialize", params: {} });
    const plain = reply(without.target.sent, 1)?.result as Record<string, unknown> | undefined;
    const bare = plain?.hostCapabilities as Record<string, unknown> | undefined;
    expect(bare?.serverResources).toBeUndefined();
  });

  it("refuses ui/message while the tool call is paused, and says why", () => {
    const { target, from } = host();
    from({
      jsonrpc: "2.0",
      id: 11,
      method: "ui/message",
      params: { role: "user", content: { type: "text", text: "hello" } },
    });
    // Nothing can enter the conversation mid-interrupt, so the app is told
    // instead of having its message silently dropped.
    const e = reply(target.sent, 11)?.error as { message: string } | undefined;
    expect(e?.message).toMatch(/paused/i);
  });

  it("passes a message through when the surface can accept one", () => {
    const onMessage = vi.fn();
    const { target, from } = host({ onMessage });
    from({
      jsonrpc: "2.0",
      id: 12,
      method: "ui/message",
      params: { role: "user", content: { type: "text", text: "  hello  " } },
    });
    expect(onMessage).toHaveBeenCalledWith("hello");
    expect(reply(target.sent, 12)?.result).toEqual({});
  });

  it("replaces model context rather than accumulating it", () => {
    const onModelContext = vi.fn();
    const { from } = host({ onModelContext });
    from({
      jsonrpc: "2.0",
      id: 13,
      method: "ui/update-model-context",
      params: { structuredContent: { picked: "a" } },
    });
    from({
      jsonrpc: "2.0",
      id: 14,
      method: "ui/update-model-context",
      params: { structuredContent: { picked: "b" } },
    });
    // Each call overwrites the previous, per the spec, so the handler sees whole
    // values and never a delta to merge.
    expect(onModelContext).toHaveBeenNthCalledWith(1, {
      content: undefined,
      structuredContent: { picked: "a" },
    });
    expect(onModelContext).toHaveBeenNthCalledWith(2, {
      content: undefined,
      structuredContent: { picked: "b" },
    });
  });

  it("asks the View to shut down before the frame goes", () => {
    const { bridge, target } = host();
    bridge.teardown(target.win, "The pause was resolved.");
    const bye = sent(target.sent, "ui/resource-teardown");
    // A request, not a notification: the View gets a chance to save what the
    // person typed, which means it needs an id to answer.
    expect(bye?.id).toBeTruthy();
    expect((bye?.params as { reason: string } | undefined)?.reason).toMatch(/resolved/i);
  });
});
