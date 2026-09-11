/**
 * The tool-name prefix, which has to agree with the backend or nothing matches.
 *
 * `custom_demo/runtime/mcp_servers.py` namespaces every remote tool as
 * `{id}_{tool}`, deriving the id with its own `slugify`. The SPA has to derive
 * the SAME id to recognise a remote tool in the stream. Nothing enforces that
 * across the two languages, and when it drifted the failure was silent: tools
 * ran fine and no MCP App ever rendered, because the prefix list matched
 * nothing at all.
 *
 * The cases below mirror `slugify`'s behaviour, including its "mcp" fallback.
 */
import { describe, expect, it } from "vitest";
import { mcpServerId } from "./api";

describe("deriving a server's tool prefix", () => {
  it("uses an explicit id when the server has one", () => {
    expect(mcpServerId({ id: "meridian", label: "Meridian Wealth", url: "" })).toBe("meridian");
  });

  it("falls back to the label, which is the usual case", () => {
    // `id` is optional on a saved server while the backend always computes one,
    // so a config that never went through Settings still has to resolve.
    expect(mcpServerId({ label: "Excalidraw", url: "https://mcp.excalidraw.com/mcp" })).toBe(
      "excalidraw",
    );
  });

  it("slugifies the way the backend does", () => {
    expect(mcpServerId({ label: "Meridian Wealth", url: "" })).toBe("meridian_wealth");
    expect(mcpServerId({ label: "  ACME-Corp!  ", url: "" })).toBe("acme_corp");
    expect(mcpServerId({ label: "a.b.c", url: "" })).toBe("a_b_c");
  });

  it("falls back to the url, then to 'mcp', rather than returning nothing", () => {
    // An unnamed server still needs a prefix; an empty one would make every
    // tool name match.
    expect(mcpServerId({ label: "", url: "https://x.ngrok.app/mcp" })).toBe(
      "https_x_ngrok_app_mcp",
    );
    expect(mcpServerId({ label: "", url: "" })).toBe("mcp");
    expect(mcpServerId({ label: "!!!", url: "" })).toBe("mcp");
  });

  it("produces a prefix a namespaced tool name actually starts with", () => {
    // The whole point: this is how a remote tool is told from a local one.
    const prefix = `${mcpServerId({ label: "Excalidraw", url: "" })}_`;
    expect("excalidraw_create_view".startsWith(prefix)).toBe(true);
    expect("write_file".startsWith(prefix)).toBe(false);
  });
});
