/**
 * Putting a conversation back after a refresh.
 *
 * The thread id lives in the URL, so the history survives; this is the mapping
 * that turns it back into cards. Two things make it non-obvious. A finished
 * tool call carries no UI metadata, so only the bootstrap map can say which
 * ones were apps. And a tool result's structured half lives on the message's
 * artifact, not in its text.
 */
import { describe, expect, it } from "vitest";
import { rehydrateItems, structuredFromToolMessage } from "./rehydrate";
import type { ThreadMessage } from "@/lib/api";

const APPS = { draw_create_view: { resourceUri: "ui://excalidraw/app.html" } };

const HISTORY: ThreadMessage[] = [
  { id: "m1", type: "human", content: "draw me a flow chart" },
  {
    id: "m2",
    type: "ai",
    content: "Sure, opening the canvas.",
    tool_calls: [
      { id: "c1", name: "draw_create_view", args: { elements: "[]" } },
      { id: "c2", name: "draw_read_me", args: {} },
    ],
  },
  { id: "m3", type: "tool", tool_call_id: "c1", content: '{"checkpoint":"abc"}' },
  { id: "m4", type: "tool", tool_call_id: "c2", content: "format reference" },
  { id: "m5", type: "ai", content: "Done." },
];

describe("rehydrating a thread", () => {
  it("rebuilds questions, answers and apps in the order they happened", () => {
    const items = rehydrateItems(HISTORY, APPS);
    expect(items.map((i) => `${i.kind}:${i.id}`)).toEqual([
      "user:m1",
      // Narration before the card: the model writes the sentence first, and the
      // card belongs under the sentence that introduced it.
      "assistant:m2",
      "app:app:c1",
      "assistant:m5",
    ]);
  });

  it("only rebuilds a card for a tool the bootstrap map knows", () => {
    const items = rehydrateItems(HISTORY, APPS);
    // `draw_read_me` is a real MCP tool from the same server and ships no UI.
    // The prefix test the map replaced could not have told these two apart, so
    // a refresh would have mounted a card for both.
    expect(items.filter((i) => i.kind === "app")).toHaveLength(1);
    expect(rehydrateItems(HISTORY, {}).some((i) => i.kind === "app")).toBe(false);
  });

  it("attaches each result to the card its tool call opened", () => {
    const app = rehydrateItems(HISTORY, APPS).find((i) => i.kind === "app");
    expect(app?.kind === "app" && app.toolResult?.structuredContent).toEqual({
      checkpoint: "abc",
    });
    // Never left mid-stream: a restored call has already finished, so a card
    // that still said `streaming` would sit waiting for partials that cannot come.
    expect(app?.kind === "app" && app.streaming).toBe(false);
  });

  it("carries the arguments through, so the app can draw", () => {
    const app = rehydrateItems(HISTORY, APPS).find((i) => i.kind === "app");
    expect(app?.kind === "app" && app.toolArgs).toEqual({ elements: "[]" });
  });

  it("skips empty messages rather than leaving blank bubbles", () => {
    expect(rehydrateItems([{ id: "x", type: "ai", content: "   " }], APPS)).toEqual([]);
  });
});

describe("a tool result's structured half", () => {
  it("comes off the artifact langchain.mcp actually sets", () => {
    // `_convert_call_tool_result` puts `structuredContent` on the ToolMessage as
    // `MCPToolArtifact(structured_content=...)`. Reading it is the only thing
    // that is right when the model-facing text differs from the UI's data,
    // which the spec explicitly encourages.
    const msg = {
      type: "tool",
      content: "Diagram displayed. Checkpoint e4a3a5cd.",
      artifact: { structured_content: { checkpoint: "e4a3a5cd", elements: 12 } },
    } as ThreadMessage;
    expect(structuredFromToolMessage(msg)).toEqual({ checkpoint: "e4a3a5cd", elements: 12 });
  });

  it("falls back to parsing the text when there is no artifact", () => {
    expect(structuredFromToolMessage({ type: "tool", content: '{"a":1}' })).toEqual({ a: 1 });
  });

  it("is undefined for a text-only result", () => {
    // The honest answer. An app that reads `structuredContent` should see
    // nothing rather than a shape invented from prose.
    expect(structuredFromToolMessage({ type: "tool", content: "all done" })).toBeUndefined();
  });
});
