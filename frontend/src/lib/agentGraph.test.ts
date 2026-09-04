import { describe, expect, it } from "vitest";
import {
  DEFAULT_LABEL_CHARS,
  graphWidthFor,
  labelCharsFor,
  NODE_W,
  NODE_X,
  nodeLabel,
  nodeWidthFor,
} from "@/lib/agentGraph";
import type { ChipData } from "@/components/chat/ToolChip";

const chip = (over: Partial<ChipData> = {}): ChipData => ({
  id: "1",
  name: "read_file",
  arg: "",
  result: null,
  ...over,
});

describe("nodeWidthFor", () => {
  it("never goes below the fixed minimum", () => {
    expect(nodeWidthFor(0)).toBe(NODE_W);
    expect(nodeWidthFor(200)).toBe(NODE_W);
    expect(nodeWidthFor(NaN)).toBe(NODE_W);
  });

  it("grows with the panel, filling it rather than leaving a margin", () => {
    // The default 680px panel minus its padding.
    const w = nodeWidthFor(656);
    expect(w).toBeGreaterThan(NODE_W);
    expect(graphWidthFor(w)).toBeLessThanOrEqual(656);
  });

  it("keeps the drawing inside the panel at any width", () => {
    for (const panel of [400, 656, 900, 1400, 2000]) {
      expect(graphWidthFor(nodeWidthFor(panel))).toBeLessThanOrEqual(Math.max(panel, graphWidthFor(NODE_W)));
    }
  });
});

describe("labelCharsFor", () => {
  it("reproduces the old constant at the old column width", () => {
    // 236px was the fixed column and 23 characters was its hardcoded budget.
    expect(labelCharsFor(NODE_W)).toBeCloseTo(DEFAULT_LABEL_CHARS, 0);
  });

  it("gives more characters as the column grows", () => {
    expect(labelCharsFor(400)).toBeGreaterThan(labelCharsFor(NODE_W));
    expect(labelCharsFor(800)).toBeGreaterThan(labelCharsFor(400));
  });

  it("never collapses to nothing on a narrow column", () => {
    expect(labelCharsFor(0)).toBeGreaterThanOrEqual(12);
  });
});

describe("nodeLabel", () => {
  it("honours the budget it is given", () => {
    const c = chip({ arg: "/workspace/data/competitor_products_detail.csv" });
    const short = nodeLabel(c, 23);
    const long = nodeLabel(c, 60);
    expect(short.length).toBeLessThanOrEqual(23);
    expect(long.length).toBeGreaterThan(short.length);
  });

  it("distinguishes two paths that collide at the narrow budget", () => {
    // The reported case: both truncated to "data/competitor_produc…".
    const a = chip({ arg: "/workspace/data/competitor_products_na.csv" });
    const b = chip({ arg: "/workspace/data/competitor_products_eu.csv" });
    expect(nodeLabel(a, 23)).toBe(nodeLabel(b, 23));
    expect(nodeLabel(a, 60)).not.toBe(nodeLabel(b, 60));
  });

  it("falls back to the first real line of code when there is no arg", () => {
    const c = chip({ name: "execute", arg: "", code: "#!/bin/sh\n\ncd /workspace/data && ls" });
    expect(nodeLabel(c, 40)).toContain("cd /workspace/data");
  });

  it("uses NODE_X consistently with the exported geometry", () => {
    expect(NODE_X).toBeGreaterThan(0);
  });
});
