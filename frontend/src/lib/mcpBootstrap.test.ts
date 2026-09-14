// @vitest-environment jsdom
/**
 * The answer that replaces the SPA's guess about which tools ship a UI.
 *
 * `_meta.ui.resourceUri` lives on `tools/list`, which needs an MCP client,
 * which only the deployment has. Before this the SPA matched a tool name
 * against `{server}_` prefixes, which answers "is this an MCP tool" and not
 * "does it have a UI", so it mounted a card for every remote call and was
 * usually told null.
 *
 * The cases that matter are the ones that would silently stop apps rendering:
 * a remembered failure, and a stale answer after the servers change.
 */
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { clearMcpAppsCache, fetchMcpApps, fetchMcpCatalog } from "./api";

const SERVERS = [{ id: "draw", label: "Draw", url: "https://a.example/mcp" }];
const CATALOG = [
  {
    id: "draw",
    label: "Draw",
    ok: true,
    tools: [
      { name: "draw_read_me", description: "Format reference", app: null },
      { name: "draw_create_view", app: "ui://excalidraw/app.html", inputSchema: { type: "object" } },
    ],
  },
];
const APPS = {
  draw_create_view: { resourceUri: "ui://excalidraw/app.html", inputSchema: { type: "object" }, appOnly: undefined },
};

function respond(body: unknown, ok = true) {
  const f = vi.fn(async () => ({ ok, json: async () => body }));
  vi.stubGlobal("fetch", f);
  return f;
}

beforeEach(() => clearMcpAppsCache());
afterEach(() => vi.unstubAllGlobals());

it("asks once per server set, however many turns follow", async () => {
  const f = respond({ servers: CATALOG });
  expect(await fetchMcpApps(SERVERS)).toEqual(APPS);
  await fetchMcpApps(SERVERS);
  await fetchMcpApps(SERVERS);
  // One question per server set replaces one per tool call, which was the
  // whole point of asking at all.
  expect(f).toHaveBeenCalledTimes(1);
});

it("shares one request between callers racing in the same frame", async () => {
  const f = respond({ servers: CATALOG });
  await Promise.all([fetchMcpApps(SERVERS), fetchMcpApps(SERVERS), fetchMcpApps(SERVERS)]);
  // The promise is cached, not the value: mount and warm-up fire together.
  expect(f).toHaveBeenCalledTimes(1);
});

it("re-asks when the servers change", async () => {
  const f = respond({ servers: CATALOG });
  await fetchMcpApps(SERVERS);
  await fetchMcpApps([{ ...SERVERS[0], url: "https://b.example/mcp" }]);
  // Keyed on the servers, so editing a connection in Settings cannot serve the
  // old server's bindings and render the wrong app.
  expect(f).toHaveBeenCalledTimes(2);
});

it("does not remember a failure", async () => {
  const f = respond({}, false);
  expect(await fetchMcpApps(SERVERS)).toEqual({});
  await fetchMcpApps(SERVERS);
  // An empty map means "nothing here has a UI". Caching that after a network
  // blip would stop every app rendering for the whole TTL, silently.
  expect(f).toHaveBeenCalledTimes(2);
});

it("asks nothing at all when no server is connected", async () => {
  const f = respond({ servers: [] });
  expect(await fetchMcpApps([])).toEqual({});
  expect(f).not.toHaveBeenCalled();
});

it("hands Settings the whole catalogue from the same one request", async () => {
  const f = respond({ servers: CATALOG });
  const [catalog, apps] = await Promise.all([fetchMcpCatalog(SERVERS), fetchMcpApps(SERVERS)]);
  // Every tool for the connector list, only the ones with a UI for the chat.
  // Claude's bootstrap ships the whole catalogue for exactly this reason: its
  // Excalidraw arrives with five tools of which one has a `resourceUri`.
  expect(catalog[0].tools.map((t) => t.name)).toEqual(["draw_read_me", "draw_create_view"]);
  expect(Object.keys(apps)).toEqual(["draw_create_view"]);
  expect(f).toHaveBeenCalledTimes(1);
});
