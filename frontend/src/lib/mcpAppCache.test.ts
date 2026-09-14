// @vitest-environment jsdom
/**
 * The app-document cache, which exists because the documents are large.
 *
 * Excalidraw's app is 432KB and ours is 618KB, neither changes between renders,
 * and the deployment opens a fresh `resources/read` for every request. Before
 * this, each card that mounted pulled the whole document again.
 *
 * The interesting cases are all about NOT over-caching: a failure must not stick
 * for two minutes, and a different server behind the same tool name must not be
 * served the old one's HTML.
 */
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { clearMcpAppCache, fetchMcpApp } from "./api";

const SERVERS = [{ id: "meridian", label: "Meridian", url: "https://a.example/mcp" }];
const APP = { tool_name: "meridian_x", resource_uri: "ui://m/a.html", mime_type: "text/html", html: "<p>app</p>" };

function respond(times: number) {
  const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ app: APP }) }));
  vi.stubGlobal("fetch", fetchMock);
  void times;
  return fetchMock;
}

beforeEach(() => clearMcpAppCache());
afterEach(() => vi.unstubAllGlobals());

it("fetches once and serves the rest from cache", async () => {
  const f = respond(1);
  await fetchMcpApp(SERVERS, "meridian_x");
  await fetchMcpApp(SERVERS, "meridian_x");
  await fetchMcpApp(SERVERS, "meridian_x");
  expect(f).toHaveBeenCalledTimes(1);
});

it("shares one request between cards mounting in the same frame", async () => {
  const f = respond(1);
  // Not awaited between calls: this is the real case, three cards rendering at
  // once, and without promise caching all three would race.
  const all = await Promise.all([
    fetchMcpApp(SERVERS, "meridian_x"),
    fetchMcpApp(SERVERS, "meridian_x"),
    fetchMcpApp(SERVERS, "meridian_x"),
  ]);
  expect(f).toHaveBeenCalledTimes(1);
  expect(all.every((a) => a?.html === "<p>app</p>")).toBe(true);
});

it("does not serve one server's HTML for another's", async () => {
  const f = respond(2);
  await fetchMcpApp(SERVERS, "meridian_x");
  // Same tool name, connection edited in Settings. Keying on the tool alone
  // would hand back the previous server's document.
  await fetchMcpApp([{ ...SERVERS[0], url: "https://b.example/mcp" }], "meridian_x");
  expect(f).toHaveBeenCalledTimes(2);
});

it("does not remember a failure", async () => {
  const f = vi.fn(async () => {
    throw new Error("network");
  });
  vi.stubGlobal("fetch", f);
  expect(await fetchMcpApp(SERVERS, "meridian_x")).toBeNull();
  // A blip must not suppress the app for the whole TTL.
  expect(await fetchMcpApp(SERVERS, "meridian_x")).toBeNull();
  expect(f).toHaveBeenCalledTimes(2);
});

it("remembers a tool that has no app, which is most of them", async () => {
  const f = vi.fn(async () => ({ ok: true, json: async () => ({ app: null }) }));
  vi.stubGlobal("fetch", f);
  expect(await fetchMcpApp(SERVERS, "meridian_x")).toBeNull();
  expect(await fetchMcpApp(SERVERS, "meridian_x")).toBeNull();
  // Re-asking on every render is the commoner waste.
  expect(f).toHaveBeenCalledTimes(1);
});
