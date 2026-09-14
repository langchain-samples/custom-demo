/**
 * The rules SEP-1865 makes a HOST enforce, now that the host is this page.
 *
 * They used to live in `custom_demo/runtime/mcp_servers.py`, because the only
 * MCP client was the deployment's. The browser has its own now, reaching each
 * server through a proxy that forwards bytes and enforces nothing, so these
 * moved here with it.
 *
 * That is safe for one reason and it is worth stating plainly: a view has an
 * opaque origin and no network, so `postMessage` to this page is its only way
 * out and it cannot reach the proxy behind our back. Give a view network
 * access and these stop being boundaries.
 */
import { describe, expect, it } from "vitest";
import { appCallable, modelVisible, resolveAppCall, toolAppUri } from "./mcpClients";

const tool = (ui?: Record<string, unknown>, flat?: string) =>
  ({ name: "t", inputSchema: {}, _meta: { ...(ui ? { ui } : {}), ...(flat ? { "ui/resourceUri": flat } : {}) } }) as never;

describe("which tools ship a UI", () => {
  it("reads the current key", () => {
    expect(toolAppUri(tool({ resourceUri: "ui://a/b.html" }))).toBe("ui://a/b.html");
  });

  it("reads the deprecated flat key, which real servers still send", () => {
    // SEP-1865 deprecates `_meta["ui/resourceUri"]` but keeps it until GA, and
    // Excalidraw sends both. Ignoring it shows a generic form for a server
    // that does ship a UI, with nothing saying why.
    expect(toolAppUri(tool(undefined, "ui://a/b.html"))).toBe("ui://a/b.html");
  });

  it("prefers the current key when a server sends both", () => {
    expect(toolAppUri(tool({ resourceUri: "ui://new.html" }, "ui://old.html"))).toBe("ui://new.html");
  });

  it("is undefined for an ordinary tool, and for a uri that is not ui://", () => {
    expect(toolAppUri(tool())).toBeUndefined();
    expect(toolAppUri(tool({ resourceUri: "https://evil.example/x.html" }))).toBeUndefined();
  });
});

describe("visibility", () => {
  it("defaults to both when the server says nothing", () => {
    // Omitting the key means ["model", "app"], which is every ordinary tool on
    // every server that has never heard of the extension.
    expect(appCallable(tool())).toBe(true);
    expect(modelVisible(tool())).toBe(true);
  });

  it("keeps an app-only tool out of the model\'s reach", () => {
    const appOnly = tool({ visibility: ["app"] });
    expect(appCallable(appOnly)).toBe(true);
    // The MUST that matters: an allocation is where a person left five
    // sliders, and a model that can call the submit tool can skip the person.
    expect(modelVisible(appOnly)).toBe(false);
  });

  it("refuses an app calling a tool the server did not open to apps", () => {
    const modelOnly = tool({ visibility: ["model"] });
    expect(appCallable(modelOnly)).toBe(false);
    expect(modelVisible(modelOnly)).toBe(true);
  });
});

describe("which tool a view may reach", () => {
  const A = { id: "meridian", label: "Meridian", url: "https://a.example/mcp" };
  const B = { id: "other", label: "Other", url: "https://b.example/mcp" };
  const entry = (server: typeof A, name: string, ui?: Record<string, unknown>) => ({
    name: `${server.id}_${name}`,
    upstreamName: name,
    server,
    tool: { name, inputSchema: {}, _meta: ui ? { ui } : {} },
  }) as never;

  const opener = entry(A, "sign_document", { resourceUri: "ui://m/a.html" });
  const submit = entry(A, "submit_signature", { visibility: ["app"] });
  const modelOnly = entry(A, "delete_account", { visibility: ["model"] });
  const elsewhere = entry(B, "wire_funds");

  it("names tools the way the app's own server does", () => {
    // A view calls `submit_signature`, unprefixed, because that is what its
    // server published. The `{server}_` prefix is ours, not the server's.
    expect(resolveAppCall([opener, submit], "meridian_sign_document", "submit_signature")).toBe(submit);
    // A prefixed name still resolves, for an app that knows the namespace.
    expect(resolveAppCall([opener, submit], "meridian_sign_document", "meridian_submit_signature")).toBe(submit);
  });

  it("refuses a tool the server did not open to apps", () => {
    expect(() => resolveAppCall([opener, modelOnly], "meridian_sign_document", "delete_account")).toThrow(
      /not open to apps/,
    );
  });

  it("refuses a tool on another server", () => {
    expect(() => resolveAppCall([opener, elsewhere], "meridian_sign_document", "other_wire_funds")).toThrow(
      /different servers/,
    );
  });

  it("refuses a tool nobody has", () => {
    expect(() => resolveAppCall([opener], "meridian_sign_document", "nope")).toThrow(/not a tool on meridian/);
  });
});
