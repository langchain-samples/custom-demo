import { describe, expect, it } from "vitest";
import {
  clampRect,
  defaultRect,
  isPanelRect,
  MIN_H,
  MIN_W,
  type PanelRect,
} from "@/lib/floatingPanel";

const LAPTOP = { w: 1280, h: 800 };

describe("defaultRect", () => {
  it("sits at the bottom left, clear of the header", () => {
    const r = defaultRect(LAPTOP.w, LAPTOP.h);
    expect(r.x).toBe(24);
    expect(r.y).toBeGreaterThanOrEqual(72);
    expect(r.y + r.h).toBeLessThanOrEqual(LAPTOP.h);
  });

  it("shrinks to fit a small viewport instead of overflowing", () => {
    const r = defaultRect(500, 400);
    expect(r.w).toBeLessThanOrEqual(500);
    expect(r.w).toBeGreaterThanOrEqual(MIN_W);
    expect(r.h).toBeGreaterThanOrEqual(MIN_H);
  });
});

describe("clampRect", () => {
  it("leaves a rect that already fits alone", () => {
    const r: PanelRect = { x: 100, y: 100, w: 600, h: 400 };
    expect(clampRect(r, LAPTOP.w, LAPTOP.h)).toEqual(r);
  });

  it("rescues a position saved on a bigger monitor", () => {
    // Saved at the right of a 2560-wide display, reopened on a laptop.
    const r: PanelRect = { x: 2000, y: 1200, w: 620, h: 460 };
    const c = clampRect(r, LAPTOP.w, LAPTOP.h);
    expect(c.x).toBeLessThan(LAPTOP.w);
    expect(c.y).toBeLessThan(LAPTOP.h);
  });

  it("always leaves something grabbable on screen", () => {
    for (const r of [
      { x: -5000, y: -5000, w: 600, h: 400 },
      { x: 99999, y: 99999, w: 600, h: 400 },
    ] as PanelRect[]) {
      const c = clampRect(r, LAPTOP.w, LAPTOP.h);
      expect(c.x + c.w).toBeGreaterThan(0); // some of it is right of the left edge
      expect(c.x).toBeLessThan(LAPTOP.w); // and it starts before the right edge
      expect(c.y).toBeGreaterThanOrEqual(0); // the drag handle is never above the top
      expect(c.y).toBeLessThan(LAPTOP.h);
    }
  });

  it("never returns a y above zero, since the title bar is the drag handle", () => {
    expect(clampRect({ x: 10, y: -300, w: 600, h: 400 }, LAPTOP.w, LAPTOP.h).y).toBe(0);
  });

  it("enforces the minimum size", () => {
    const c = clampRect({ x: 0, y: 0, w: 10, h: 10 }, LAPTOP.w, LAPTOP.h);
    expect(c.w).toBe(MIN_W);
    expect(c.h).toBe(MIN_H);
  });

  it("caps size to the viewport", () => {
    const c = clampRect({ x: 0, y: 0, w: 9000, h: 9000 }, LAPTOP.w, LAPTOP.h);
    expect(c.w).toBeLessThanOrEqual(LAPTOP.w);
    expect(c.h).toBeLessThanOrEqual(LAPTOP.h);
  });

  it("clamps size before position, so a too-wide rect is not pinned left", () => {
    // w exceeds the viewport; after the size clamp there is still room to honour x.
    const c = clampRect({ x: 300, y: 100, w: 5000, h: 400 }, LAPTOP.w, LAPTOP.h);
    expect(c.w).toBeLessThanOrEqual(LAPTOP.w);
    expect(c.x).toBeLessThan(LAPTOP.w);
  });

  it("is idempotent, so reopening repeatedly does not creep", () => {
    const once = clampRect({ x: 2000, y: 1200, w: 620, h: 460 }, LAPTOP.w, LAPTOP.h);
    expect(clampRect(once, LAPTOP.w, LAPTOP.h)).toEqual(once);
  });
});

describe("isPanelRect", () => {
  it("accepts a full rect", () => {
    expect(isPanelRect({ x: 1, y: 2, w: 3, h: 4 })).toBe(true);
  });

  it("rejects the shapes localStorage actually hands back when things go wrong", () => {
    expect(isPanelRect(null)).toBe(false);
    expect(isPanelRect("{}")).toBe(false);
    expect(isPanelRect({ x: 1, y: 2, w: 3 })).toBe(false);
    expect(isPanelRect({ x: 1, y: 2, w: 3, h: "4" })).toBe(false);
    expect(isPanelRect({ x: NaN, y: 2, w: 3, h: 4 })).toBe(false);
  });
});
