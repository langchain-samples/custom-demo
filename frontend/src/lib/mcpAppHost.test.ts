// @vitest-environment jsdom
/**
 * Our half of the MCP Apps host: everything the SDK does not decide.
 *
 * `AppBridge` now owns the wire format, version negotiation and message
 * ordering, so re-testing those would be testing someone else's library. What
 * is still ours, and what these cover, is the host context we hand it, which
 * capabilities we claim, and how each handler answers, including the refusals.
 *
 * The bridge is mocked rather than driven over a real `postMessage`: the
 * handlers are the unit under test, and calling them directly says more than
 * asserting on frames. The app's own half is exercised for real against a built
 * app in `custom_demo/tests/signature_app_test.js`.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const built = vi.hoisted(() => ({ current: null as null | Record<string, unknown> }));

vi.mock("@modelcontextprotocol/ext-apps/app-bridge", () => ({
  PostMessageTransport: class {},
  AppBridge: class {
    // Plain assignment, not parameter properties: this repo compiles with
    // `erasableSyntaxOnly`, which forbids the shorthand.
    client: unknown;
    info: unknown;
    capabilities: Record<string, unknown>;
    options: { hostContext: Record<string, unknown> };
    constructor(
      client: unknown,
      info: unknown,
      capabilities: Record<string, unknown>,
      options: { hostContext: Record<string, unknown> },
    ) {
      this.client = client;
      this.info = info;
      this.capabilities = capabilities;
      this.options = options;
      built.current = this as unknown as Record<string, unknown>;
    }
    connect = vi.fn(async () => {});
    sendToolInput = vi.fn(async () => {});
    sendToolInputPartial = vi.fn(async () => {});
    sendToolResult = vi.fn(async () => {});
    sendHostContextChange = vi.fn(async () => {});
    teardownResource = vi.fn(async () => ({}));
  },
}));

const { createMcpAppHost } = await import("./mcpAppHost");

type Cfg = Parameters<typeof createMcpAppHost>[0];

function host(overrides: Partial<Cfg> = {}) {
  const api = createMcpAppHost({ toolName: "meridian_sign_document", onHeight: () => {}, ...overrides });
  const bridge = built.current as Record<string, never>;
  return { api, bridge };
}

beforeEach(() => (built.current = null));

describe("the host context we hand the SDK", () => {
  it("carries a COMPLETE tool, schema included", () => {
    const schema = { type: "object", properties: { elements: { type: "string" } } };
    const { bridge } = host({ toolInputSchema: schema });
    const tool = (bridge.options as never as { hostContext: never }).hostContext as never as {
      toolInfo: { tool: { name: string; inputSchema: unknown } };
    };
    // `inputSchema` is required by `Tool`, and Excalidraw's app rejected the
    // whole handshake when we left it out.
    expect(tool.toolInfo.tool.name).toBe("meridian_sign_document");
    expect(tool.toolInfo.tool.inputSchema).toEqual(schema);
  });

  it("falls back to an empty object schema, never to nothing", () => {
    const { bridge } = host();
    const ctx = (bridge.options as never as { hostContext: never }).hostContext as never as {
      toolInfo: { tool: { inputSchema: unknown } };
    };
    expect(ctx.toolInfo.tool.inputSchema).toEqual({ type: "object" });
  });

  it("advertises exactly the display modes this surface supports", () => {
    const { bridge } = host({ displayModes: ["inline", "fullscreen"] });
    const ctx = (bridge.options as never as { hostContext: never }).hostContext as never as {
      availableDisplayModes: string[];
    };
    // A view MUST check this before asking, so understating it is what makes a
    // button like Excalidraw's Edit sit there doing nothing.
    expect(ctx.availableDisplayModes).toEqual(["inline", "fullscreen"]);
  });
});

describe("the capabilities we claim", () => {
  it("claims a proxy only when one is wired", () => {
    const bare = host().bridge.capabilities as Record<string, unknown>;
    // Claiming a capability and then refusing it is worse than never claiming.
    expect(bare.serverTools).toBeUndefined();
    expect(bare.serverResources).toBeUndefined();

    const wired = host({ onToolCall: async () => ({}), onReadResource: async () => [] }).bridge
      .capabilities as Record<string, unknown>;
    expect(wired.serverTools).toBeTruthy();
    expect(wired.serverResources).toBeTruthy();
  });
});

describe("the handlers, including what they refuse", () => {
  it("proxies a tools/call and returns its result", async () => {
    const onToolCall = vi.fn(async () => ({ structuredContent: { reference: "MW-DOC-1" } }));
    const { bridge } = host({ onToolCall });
    const out = await (bridge.oncalltool as (p: unknown) => Promise<{ structuredContent: unknown }>)({
      name: "meridian_submit_signature",
      arguments: { signed_by: "Grace" },
    });
    expect(onToolCall).toHaveBeenCalledWith("meridian_submit_signature", { signed_by: "Grace" });
    expect(out.structuredContent).toEqual({ reference: "MW-DOC-1" });
  });

  it("rejects rather than hanging when nothing is wired", async () => {
    const { bridge } = host();
    // A dropped request is a button that hangs, which is worse than a refusal.
    await expect((bridge.oncalltool as (p: unknown) => Promise<unknown>)({ name: "x" })).rejects.toThrow(
      /does not proxy tool calls/,
    );
    await expect(
      (bridge.onreadresource as (p: unknown) => Promise<unknown>)({ uri: "tips://x" }),
    ).rejects.toThrow(/does not proxy resources/);
    await expect(
      (bridge.onmessage as (p: unknown) => Promise<unknown>)({ content: { text: "hi" } }),
    ).rejects.toThrow(/cannot accept a message/);
  });

  it("opens an http link and refuses any other scheme", async () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    const { bridge } = host();
    await (bridge.onopenlink as (p: unknown) => Promise<unknown>)({ url: "https://example.com" });
    expect(open).toHaveBeenCalled();

    open.mockClear();
    // javascript: would run in THIS page, which is the point of the sandbox.
    await expect(
      (bridge.onopenlink as (p: unknown) => Promise<unknown>)({ url: "javascript:alert(1)" }),
    ).rejects.toThrow(/http and https/);
    expect(open).not.toHaveBeenCalled();
    open.mockRestore();
  });

  it("grants a mode it supports and reports the resulting one either way", async () => {
    const onDisplayMode = vi.fn();
    const { bridge } = host({ displayModes: ["inline", "fullscreen"], onDisplayMode });
    const ask = bridge.onrequestdisplaymode as (p: unknown) => Promise<{ mode: string }>;

    expect(await ask({ mode: "fullscreen" })).toEqual({ mode: "fullscreen" });
    expect(onDisplayMode).toHaveBeenCalledWith("fullscreen");
    // Not supported here, so the CURRENT mode comes back, not the request.
    expect(await ask({ mode: "pip" })).toEqual({ mode: "fullscreen" });
  });

  it("replaces model context rather than accumulating it", async () => {
    const onModelContext = vi.fn();
    const { bridge } = host({ onModelContext });
    const send = bridge.onupdatemodelcontext as (p: unknown) => Promise<unknown>;
    await send({ structuredContent: { picked: "a" } });
    await send({ structuredContent: { picked: "b" } });
    expect(onModelContext).toHaveBeenNthCalledWith(2, {
      content: undefined,
      structuredContent: { picked: "b" },
    });
  });
});

describe("feeding the call in", () => {
  it("sends partials while streaming and the complete arguments once", () => {
    const { api, bridge } = host();
    api.setToolInput({ elements: "[{" }, false);
    api.setToolInput({ elements: "[{a:1}]" }, true);
    // The difference between a diagram that draws itself and one that appears
    // finished. Ordering after this point is the SDK's to enforce.
    expect(bridge.sendToolInputPartial).toHaveBeenCalledTimes(1);
    expect(bridge.sendToolInput).toHaveBeenCalledWith({ arguments: { elements: "[{a:1}]" } });
  });

  it("notifies the view when the HOST changes the mode", () => {
    const { api, bridge } = host({ displayModes: ["inline", "fullscreen"] });
    api.setDisplayMode("fullscreen");
    // Pressing Escape is the host's decision, and the app has to hear about it.
    expect(bridge.sendHostContextChange).toHaveBeenCalledWith({ displayMode: "fullscreen" });
  });

  it("asks the view to shut down before the frame goes", () => {
    const { api, bridge } = host();
    api.teardown("The app was closed.");
    expect(bridge.teardownResource).toHaveBeenCalledWith({ reason: "The app was closed." });
  });
});
