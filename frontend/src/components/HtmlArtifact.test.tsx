// @vitest-environment jsdom
/**
 * The skeleton's growth, in a real DOM.
 *
 * Reasoning about this from the source failed twice: the clock, the reveal maths and the
 * mount tree all read correctly while the pane sat at one row on screen. What the source
 * cannot show is what React does to component state across a re-render with new props,
 * which is exactly where the bug turned out to live, so this test drives the component.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render } from "@testing-library/react";
import { HtmlArtifact } from "@/components/HtmlArtifact";

/** Rows the skeleton is currently showing. */
function rowCount(root: HTMLElement): number {
  const skeleton = root.querySelector("[aria-hidden]");
  return skeleton ? skeleton.children.length : 0;
}

/** A document that has begun but has nothing paintable yet - the building case. */
const HEAD_ONLY = "<!doctype html><html><head><style>body{margin:0}</style>";

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("the artifact skeleton", () => {
  it("starts at one row", () => {
    const { container } = render(
      <HtmlArtifact path="/workspace/artifacts/a.html" content={HEAD_ONLY} streaming />,
    );
    expect(rowCount(container)).toBe(1);
  });

  it("grows as the seconds pass", () => {
    const { container } = render(
      <HtmlArtifact path="/workspace/artifacts/a.html" content={HEAD_ONLY} streaming />,
    );
    act(() => void vi.advanceTimersByTime(10_000));
    expect(rowCount(container)).toBeGreaterThan(1);
    act(() => void vi.advanceTimersByTime(20_000));
    expect(rowCount(container)).toBe(9);
  });

  it("keeps growing while content streams in", () => {
    // The regression: a chunk arriving is a re-render with new props, and the skeleton
    // must not restart from one row every time the agent writes another few hundred
    // bytes of <head>. This is what the pane actually does during a real write.
    const { container, rerender } = render(
      <HtmlArtifact path="/workspace/artifacts/a.html" content={HEAD_ONLY} streaming />,
    );
    for (let i = 0; i < 10; i++) {
      act(() => void vi.advanceTimersByTime(1_000));
      rerender(
        <HtmlArtifact
          path="/workspace/artifacts/a.html"
          content={HEAD_ONLY + `<!--${"x".repeat(i * 50)}-->`}
          streaming
        />,
      );
    }
    expect(rowCount(container)).toBeGreaterThan(1);
  });
});
