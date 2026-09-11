// @vitest-environment jsdom
/**
 * The MCP elicitation card: which UI a pause gets, and what it resumes with.
 *
 * The resume shape is the part worth pinning. `langchain.mcp` matches answers to
 * the server's own request keys, so an answer under the wrong key (or a missing
 * one) fails the resume rather than degrading, and the run stays stuck. Driving
 * the component is also the only way to check the iframe's sandbox, which is the
 * boundary between us and HTML a remote server wrote.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { McpElicitationCard } from "./McpElicitationCard";
import { describeInterrupt } from "./helpers";
import { isMcpElicitation, type ReviewInterrupt } from "@/lib/api";

const fetchMcpApp = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  fetchMcpApp,
}));

const SERVERS = [{ id: "fieldlink", label: "Fieldlink", url: "https://x.ngrok.app/mcp" }];

const FORM_PAUSE: ReviewInterrupt = {
  type: "mcp_elicitation",
  tool_name: "fieldlink_schedule_delivery",
  requests: [
    {
      key: "slot",
      message: "When should FL-4417 be delivered?",
      mode: "form",
      requested_schema: {
        type: "object",
        required: ["delivery_date", "window"],
        properties: {
          delivery_date: { type: "string", format: "date", title: "Delivery date" },
          window: { type: "string", enum: ["morning", "afternoon"], title: "Window" },
        },
      },
    },
  ],
};

const APP_PAUSE: ReviewInterrupt = {
  type: "mcp_elicitation",
  tool_name: "fieldlink_collect_signature",
  requests: [
    { key: "signature", message: "Sign for FL-4501.", mode: "form", requested_schema: {} },
  ],
};

/** The card resolves its app lookup asynchronously; wait that out before asserting. */
const settled = () => screen.findByText(/FL-4417|FL-4501/);

beforeEach(() => fetchMcpApp.mockReset());
afterEach(cleanup);

describe("recognising an MCP pause", () => {
  it("tells an MCP elicitation apart from our own review pauses", () => {
    expect(isMcpElicitation(FORM_PAUSE)).toBe(true);
    expect(isMcpElicitation({ kind: "email_draft", draft: {} })).toBe(false);
    expect(isMcpElicitation(null)).toBe(false);
  });

  it("reads the server's own question out for voice mode", () => {
    expect(describeInterrupt(FORM_PAUSE)).toBe("When should FL-4417 be delivered?");
  });
});

describe("a server with no UI of its own", () => {
  it("builds a form from the requested schema and resumes under the request key", async () => {
    fetchMcpApp.mockResolvedValue(null);
    const onApprove = vi.fn();
    render(<McpElicitationCard review={FORM_PAUSE} servers={SERVERS} onApprove={onApprove} />);
    await settled();

    // The enum became a set of choices rather than a free-text box.
    fireEvent.click(screen.getByText("morning"));
    fireEvent.change(screen.getByLabelText("Delivery date"), {
      target: { value: "2026-09-15" },
    });
    fireEvent.click(screen.getByRole("button", { name: /send to the server/i }));

    expect(onApprove).toHaveBeenCalledWith({
      responses: {
        slot: { action: "accept", content: { delivery_date: "2026-09-15", window: "morning" } },
      },
    });
  });

  it("will not submit until the required fields are filled", async () => {
    fetchMcpApp.mockResolvedValue(null);
    render(<McpElicitationCard review={FORM_PAUSE} servers={SERVERS} onApprove={vi.fn()} />);
    await settled();
    const send = screen.getByRole("button", { name: /send to the server/i }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
  });

  it("declines rather than answering when the user skips", async () => {
    fetchMcpApp.mockResolvedValue(null);
    const onApprove = vi.fn();
    render(<McpElicitationCard review={FORM_PAUSE} servers={SERVERS} onApprove={onApprove} />);
    await settled();
    fireEvent.click(screen.getByRole("button", { name: /skip/i }));
    expect(onApprove).toHaveBeenCalledWith({ responses: { slot: { action: "decline" } } });
  });
});

describe("the boundary with MCP Apps", () => {
  it("renders the generic form even when the tool ships a ui:// app", async () => {
    // An app is bound to a tool RESULT, so it belongs to `McpAppCard` and
    // appears once the call finishes. Rendering one here would put
    // server-authored HTML in front of a paused run it cannot talk to: nothing
    // can enter the conversation mid-interrupt.
    fetchMcpApp.mockResolvedValue({
      tool_name: "fieldlink_collect_signature",
      resource_uri: "ui://fieldlink/signature.html",
      mime_type: "text/html;profile=mcp-app",
      html: "<p>signature pad</p>",
    });

    const { container } = render(
      <McpElicitationCard review={APP_PAUSE} servers={SERVERS} onApprove={vi.fn()} />,
    );
    await settled();
    expect(container.querySelector("iframe")).toBeNull();
    // The card must not even look: a lookup here would be a wasted round trip
    // on every pause.
    expect(fetchMcpApp).not.toHaveBeenCalled();
  });

  it("names the server in the header, matching the tool's namespace", async () => {
    render(<McpElicitationCard review={APP_PAUSE} servers={SERVERS} onApprove={vi.fn()} />);
    await settled();
    expect(screen.getByText(/Fieldlink needs input/i)).toBeTruthy();
  });

  it("still offers a way out when the schema is empty", async () => {
    render(<McpElicitationCard review={APP_PAUSE} servers={SERVERS} onApprove={vi.fn()} />);
    await settled();
    // Nothing to fill in, but the paused run must not be stranded.
    const skip = screen.getByRole("button", { name: /skip/i }) as HTMLButtonElement;
    expect(skip.disabled).toBe(false);
  });
});
