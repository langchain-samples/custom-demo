// @vitest-environment jsdom
/**
 * The app-document cache, which exists because the documents are large.
 *
 * Excalidraw's app is 432KB and ours is 618KB, neither changes between
 * renders, and the deployment opens a fresh `resources/read` for every
 * request. Reading one is two steps now: the bootstrap catalogue says which
 * `ui://` URI a tool renders, and `/mcp/resource` reads it. There is no
 * endpoint that does both, because the only reason one existed was that the
 * browser did not know the URI.
 *
 * The interesting cases are all about NOT over-caching: a failure must not
 * stick for two minutes, and two servers publishing the same `ui://` path must
 * not be served each other's HTML.
 */
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { clearMcpAppCache, clearMcpAppsCache, fetchMcpApp } from "./api";

const URI = "ui://meridian/a.html";
const SERVERS = [{ id: "meridian", label: "Meridian", url: "https://a.example/mcp" }];
const CATALOG = [
  { id: "meridian", ok: true, tools: [{ name: "meridian_x", app: URI, inputSchema: { type: "object" } }] },
];

/** Answers bootstrap and resource reads, counting only the document reads. */
function stub(opts: { resourceOk?: boolean; app?: string | null } = {}) {
  const reads = { bootstrap: 0, resource: 0 };
  const fetchMock = vi.fn(async (url: string, init?: { body?: string }) => {
    if (String(url).endsWith("/mcp/bootstrap")) {
      reads.bootstrap += 1;
      const tools = opts.app === null ? [{ name: "meridian_x", app: null }] : CATALOG[0].tools;
      return { ok: true, json: async () => ({ servers: [{ ...CATALOG[0], tools }] }) };
    }
    reads.resource += 1;
    if (opts.resourceOk === false) return { ok: false, json: async () => ({ error: "boom" }) };
    const uri = JSON.parse(init?.body ?? "{}").uri;
    return { ok: true, json: async () => ({ contents: [{ uri, text: `<p>${uri}</p>` }] }) };
  });
  vi.stubGlobal("fetch", fetchMock);
  return reads;
}

beforeEach(() => {
  clearMcpAppCache();
  clearMcpAppsCache();
});
afterEach(() => vi.unstubAllGlobals());

it("reads the document once and serves the rest from cache", async () => {
  const reads = stub();
  const first = await fetchMcpApp(SERVERS, "meridian_x");
  await fetchMcpApp(SERVERS, "meridian_x");
  await fetchMcpApp(SERVERS, "meridian_x");
  expect(first?.html).toContain(URI);
  expect(first?.resource_uri).toBe(URI);
  expect(reads.resource).toBe(1);
});

it("shares one request between cards mounting in the same frame", async () => {
  const reads = stub();
  await Promise.all([
    fetchMcpApp(SERVERS, "meridian_x"),
    fetchMcpApp(SERVERS, "meridian_x"),
    fetchMcpApp(SERVERS, "meridian_x"),
  ]);
  // The promise is cached, not the value, so a race collapses to one read of
  // a document that can be hundreds of kilobytes.
  expect(reads.resource).toBe(1);
});

it("does not serve one server's HTML for another's", async () => {
  const reads = stub();
  await fetchMcpApp(SERVERS, "meridian_x");
  await fetchMcpApp([{ ...SERVERS[0], url: "https://b.example/mcp" }], "meridian_x");
  // Keyed on the URI AND the server: two servers can publish the same `ui://`
  // path, and serving one's document for the other would be silent.
  expect(reads.resource).toBe(2);
});

it("does not remember a failure", async () => {
  const reads = stub({ resourceOk: false });
  expect(await fetchMcpApp(SERVERS, "meridian_x")).toBeNull();
  expect(await fetchMcpApp(SERVERS, "meridian_x")).toBeNull();
  // The next render should retry rather than inherit a network blip for the
  // whole TTL.
  expect(reads.resource).toBe(2);
});

it("costs no document read at all for a tool with no app", async () => {
  const reads = stub({ app: null });
  expect(await fetchMcpApp(SERVERS, "meridian_x")).toBeNull();
  expect(await fetchMcpApp(SERVERS, "meridian_x")).toBeNull();
  // Most tools have no app. The catalogue answers that without anyone reading
  // a document, which is the whole reason bootstrap exists.
  expect(reads.resource).toBe(0);
  expect(reads.bootstrap).toBe(1);
});
