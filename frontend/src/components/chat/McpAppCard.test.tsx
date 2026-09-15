// @vitest-environment jsdom
/**
 * The surface a third-party MCP App actually renders on.
 *
 * This is the ordinary MCP Apps flow: a tool finishes, and if it declared a
 * `ui://` resource the host draws that HTML and hands it the result. Every
 * server outside this repo works this way, so the interesting cases are the
 * negative ones. Most tools ship no UI, and a card that appeared beside all of
 * them would be worse than no card.
 *
 * The app's own half of the conversation is exercised for real against a built
 * app in `custom_demo/tests/signature_app_test.js`; the protocol is pinned in
 * `src/lib/mcpAppHost.test.ts`.
 */
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";
import { McpAppCard } from "./McpAppCard";

const bridge = vi.hoisted(() => ({ current: null as null | Record<string, ReturnType<typeof vi.fn>> }));
vi.mock("@modelcontextprotocol/ext-apps/app-bridge", () => ({
  PostMessageTransport: class {},
  AppBridge: class {
    constructor() {
      bridge.current = this as unknown as Record<string, ReturnType<typeof vi.fn>>;
    }
    connect = vi.fn(async () => {});
    sendToolInput = vi.fn(async () => {});
    sendToolInputPartial = vi.fn(async () => {});
    sendToolResult = vi.fn(async () => {});
    sendHostContextChange = vi.fn(async () => {});
    teardownResource = vi.fn(async () => ({}));
  },
}));

const readMcpApp = vi.hoisted(() => vi.fn());
vi.mock("@/lib/mcpClients", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/mcpClients")>()),
  readMcpApp,
}));

const SERVERS = [{ id: "meridian", label: "Meridian", url: "https://x.ngrok.app/mcp" }];

const APP = {
  resourceUri: "ui://meridian/rebalance.html",
  html: "<p>allocation sliders</p>",
};

function draw(overrides: Partial<Parameters<typeof McpAppCard>[0]> = {}) {
  return render(
    <McpAppCard
      toolName="meridian_propose_rebalance"
      toolArguments={{ account_id: "MW-10241" }}
      toolResult={{ structuredContent: { household: "Whitfield Family Trust" } }}
      streaming={false}
      servers={SERVERS}
      {...overrides}
    />,
  );
}

beforeEach(() => {
  readMcpApp.mockReset();
  // Cleared per test, or `waitFor` below resolves instantly against the bridge
  // a previous test built and every assertion counts the wrong card's frames.
  bridge.current = null;
});
afterEach(cleanup);

it("renders the server's HTML in a script-only sandbox", async () => {
  readMcpApp.mockResolvedValue(APP);
  const { container } = draw();

  const frame = await waitFor(() => {
    const el = container.querySelector("iframe");
    expect(el).toBeTruthy();
    return el as HTMLIFrameElement;
  });
  expect(frame.getAttribute("srcdoc")).toContain("allocation sliders");
  // No allow-same-origin: the server's HTML must not reach this page's origin.
  // Adding it here would hand a remote server the deployment token.
  expect(frame.getAttribute("sandbox")).toBe("allow-scripts");
});

it("renders nothing at all when the tool ships no UI", async () => {
  readMcpApp.mockResolvedValue(null);
  const { container } = draw();
  // Not a placeholder. Most tools have no app, so anything visible here would
  // appear beside almost every call.
  await waitFor(() => expect(readMcpApp).toHaveBeenCalled());
  expect(container.querySelector("iframe")).toBeNull();
  expect(container.textContent).toBe("");
});

it("shows the tool's own name, without the server prefix", async () => {
  readMcpApp.mockResolvedValue(APP);
  const { container } = draw();
  await waitFor(() => expect(container.querySelector("iframe")).toBeTruthy());
  // The prefix is ours, added by the ClientGroup for namespacing. The person
  // looking at this cares which tool ran, not how we avoided a name collision.
  expect(container.textContent).toContain("propose_rebalance");
  expect(container.textContent).not.toContain("meridian_propose_rebalance");
});

it("mounts while the arguments are still streaming", async () => {
  readMcpApp.mockResolvedValue(APP);
  // The whole point of mounting on the CALL: the frame has to exist before the
  // arguments finish, or a diagram cannot draw itself as they arrive.
  const { container } = draw({ streaming: true, toolResult: undefined });
  await waitFor(() => expect(container.querySelector("iframe")).toBeTruthy());
});

it("does not look for an app when no server is connected", async () => {
  readMcpApp.mockResolvedValue(APP);
  draw({ servers: [] });
  // A lookup needs a server to ask, so this would be a guaranteed round trip
  // to nothing.
  await new Promise((r) => setTimeout(r, 0));
  expect(readMcpApp).not.toHaveBeenCalled();
});

it("forwards each streamed argument frame as a partial, then one complete input", async () => {
  // The link the other tests miss. `mcpAppHost.test.ts` proves the bridge sends
  // a partial when told to; `ChatPanel` proves a `messages/partial` frame
  // patches `toolArgs`. Nothing joined them, so a card that failed to re-fire on
  // a changed argument object would still pass both and quietly render the
  // diagram in one jump, which is the exact bug this flow exists to avoid.
  readMcpApp.mockResolvedValue(APP);
  const { rerender } = draw({ streaming: true, toolResult: undefined, toolArguments: { elements: "[{a" } });
  await waitFor(() => expect(bridge.current).toBeTruthy());
  const b = bridge.current as NonNullable<typeof bridge.current>;

  // Successive accumulated frames, as the server sends them: each carries the
  // whole partial value so far, not a delta.
  for (const elements of ["[{a:1},{b", "[{a:1},{b:2},{c"]) {
    rerender(
      <McpAppCard
        toolName="meridian_propose_rebalance"
        toolArguments={{ elements }}
        streaming
        servers={SERVERS}
      />,
    );
  }

  rerender(
    <McpAppCard
      toolName="meridian_propose_rebalance"
      toolArguments={{ elements: "[{a:1},{b:2},{c:3}]" }}
      streaming={false}
      servers={SERVERS}
    />,
  );

  // Three partials, one per streamed frame, and exactly one complete. The count
  // is the assertion: one partial would mean the app draws in a single jump.
  expect(b.sendToolInputPartial).toHaveBeenCalledTimes(3);
  expect(b.sendToolInput).toHaveBeenCalledTimes(1);
  expect(b.sendToolInput).toHaveBeenCalledWith({ arguments: { elements: "[{a:1},{b:2},{c:3}]" } });
  // Ordering matters as much as the count: a partial after the complete is a
  // spec violation the SDK would reject.
  expect(b.sendToolInputPartial.mock.invocationCallOrder[2]).toBeLessThan(
    b.sendToolInput.mock.invocationCallOrder[0],
  );
});

it("waits with a skeleton until the model has written any arguments", async () => {
  // The frame is mounted on the tool CALL so the app can draw as the arguments
  // stream, which leaves a window where the document is live with nothing to
  // draw. For a canvas app that window is a black rectangle the height of the
  // pane, and it reads as broken rather than as pending.
  readMcpApp.mockResolvedValue(APP);
  const { container, rerender } = draw({ streaming: true, toolArguments: {}, toolResult: undefined });
  await waitFor(() => expect(container.querySelector("iframe")).toBeTruthy());
  const frame = container.querySelector("iframe");
  expect(container.querySelector("[aria-hidden]")).toBeTruthy();

  rerender(
    <McpAppCard
      toolName="meridian_propose_rebalance"
      toolArguments={{ elements: "[{a" }}
      streaming
      servers={SERVERS}
    />,
  );
  // Gone once there is something to draw, and the SAME iframe throughout:
  // re-parenting it would reload the document and lose the person's work.
  await waitFor(() => expect(container.querySelector("[aria-hidden]")).toBeNull());
  expect(container.querySelector("iframe")).toBe(frame);
});

it("does not put the skeleton back when a later frame parses to nothing", async () => {
  readMcpApp.mockResolvedValue(APP);
  const { container, rerender } = draw({ streaming: true, toolArguments: { a: 1 }, toolResult: undefined });
  await waitFor(() => expect(container.querySelector("[aria-hidden]")).toBeNull());

  rerender(
    <McpAppCard toolName="meridian_propose_rebalance" toolArguments={{}} streaming servers={SERVERS} />,
  );
  // Sticky on purpose: a skeleton reappearing over a drawing already on screen
  // is worse than the wait it was there to explain.
  expect(container.querySelector("[aria-hidden]")).toBeNull();
});
