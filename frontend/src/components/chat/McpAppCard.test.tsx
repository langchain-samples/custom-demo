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

const fetchMcpApp = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  fetchMcpApp,
}));

const SERVERS = [{ id: "meridian", label: "Meridian", url: "https://x.ngrok.app/mcp" }];

const APP = {
  tool_name: "meridian_propose_rebalance",
  resource_uri: "ui://meridian/rebalance.html",
  mime_type: "text/html;profile=mcp-app",
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

beforeEach(() => fetchMcpApp.mockReset());
afterEach(cleanup);

it("renders the server's HTML in a script-only sandbox", async () => {
  fetchMcpApp.mockResolvedValue(APP);
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
  fetchMcpApp.mockResolvedValue(null);
  const { container } = draw();
  // Not a placeholder. Most tools have no app, so anything visible here would
  // appear beside almost every call.
  await waitFor(() => expect(fetchMcpApp).toHaveBeenCalled());
  expect(container.querySelector("iframe")).toBeNull();
  expect(container.textContent).toBe("");
});

it("shows the tool's own name, without the server prefix", async () => {
  fetchMcpApp.mockResolvedValue(APP);
  const { container } = draw();
  await waitFor(() => expect(container.querySelector("iframe")).toBeTruthy());
  // The prefix is ours, added by the ClientGroup for namespacing. The person
  // looking at this cares which tool ran, not how we avoided a name collision.
  expect(container.textContent).toContain("propose_rebalance");
  expect(container.textContent).not.toContain("meridian_propose_rebalance");
});

it("mounts while the arguments are still streaming", async () => {
  fetchMcpApp.mockResolvedValue(APP);
  // The whole point of mounting on the CALL: the frame has to exist before the
  // arguments finish, or a diagram cannot draw itself as they arrive.
  const { container } = draw({ streaming: true, toolResult: undefined });
  await waitFor(() => expect(container.querySelector("iframe")).toBeTruthy());
});

it("does not look for an app when no server is connected", async () => {
  fetchMcpApp.mockResolvedValue(APP);
  draw({ servers: [] });
  // A lookup needs a server to ask, so this would be a guaranteed round trip
  // to nothing.
  await new Promise((r) => setTimeout(r, 0));
  expect(fetchMcpApp).not.toHaveBeenCalled();
});
