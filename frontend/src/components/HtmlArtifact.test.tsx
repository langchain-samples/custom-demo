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

/** Rows the skeleton is currently showing. Counted by class, not by child index: the
 *  container also carries a <style> block for the draw-in keyframes. */
function rowCount(root: HTMLElement): number {
  return root.querySelectorAll(".artifact-row").length;
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
  it("keeps counting across a remount, so a re-keyed tab does not restart it", () => {
    // The reported failure, which never reproduced any other way: the skeleton sat at
    // one row long past the five seconds its second row needs. Every layer tested clean,
    // so this asserts the property directly rather than a cause - however the component
    // gets torn down and rebuilt mid-write, the elapsed time is the ARTIFACT's, not the
    // instance's, and the skeleton picks up where it left off.
    const P = "/workspace/artifacts/a.html";
    const first = render(<HtmlArtifact path={P} content={HEAD_ONLY} streaming />);
    act(() => void vi.advanceTimersByTime(12_000));
    const before = rowCount(first.container);
    expect(before).toBeGreaterThan(3);
    first.unmount();

    const second = render(<HtmlArtifact path={P} content={HEAD_ONLY} streaming />);
    act(() => void vi.advanceTimersByTime(250)); // one tick, to read the clock
    expect(rowCount(second.container)).toBeGreaterThanOrEqual(before);
  });

  it("starts a fresh clock when the same path is written again", () => {
    // An edit_file to a path that already finished must not inherit the first write's
    // age and open on a nearly-full skeleton.
    const P = "/workspace/artifacts/a.html";
    const { container, rerender } = render(
      <HtmlArtifact path={P} content={HEAD_ONLY} streaming />,
    );
    act(() => void vi.advanceTimersByTime(25_000));
    rerender(<HtmlArtifact path={P} content="<html><body><p>done</p></body></html>" />);
    rerender(<HtmlArtifact path={P} content={HEAD_ONLY} streaming />);
    act(() => void vi.advanceTimersByTime(250));
    expect(rowCount(container)).toBe(1);
  });
});
