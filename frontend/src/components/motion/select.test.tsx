// @vitest-environment jsdom
/**
 * The Select settles when it mounts, in a real DOM.
 *
 * Nothing in the suite rendered a `Select` with `SelectItem` children, so the whole
 * suite stayed green while every Select on screen blanked the page: each item registers
 * its label in a layout effect, and depending on the whole context object there made the
 * registration retrigger its own effect forever (React error #185, thrown from
 * `unregister` during the commit phase). Reading the source will not show it - the loop
 * only exists once React is deciding whether an effect's deps changed - so this test
 * mounts the component and counts what the render actually did.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render } from "@testing-library/react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/motion/select";

/** SelectContent measures its own height through one; jsdom ships no implementation. */
class StubResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeEach(() => vi.stubGlobal("ResizeObserver", StubResizeObserver));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/** Counts every render of the item subtree, so a loop shows up as a number and not as a
 *  timeout. A registration pass is allowed to settle in a few renders; it is not allowed
 *  to run away. */
function Counting({ onRender, children }: { onRender: () => void; children: string }) {
  onRender();
  return <SelectItem value={children.toLowerCase()}>{children}</SelectItem>;
}

describe("Select label registration", () => {
  it("mounts with items without exceeding the update depth", () => {
    // React reports #185 by throwing out of the commit, so a plain render is the assert.
    expect(() =>
      render(
        <Select value="alpha" onValueChange={() => {}}>
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="alpha">Alpha</SelectItem>
            <SelectItem value="beta">Beta</SelectItem>
            <SelectItem value="gamma">Gamma</SelectItem>
          </SelectContent>
        </Select>,
      ),
    ).not.toThrow();
  });

  it("shows the selected item's label on the trigger", () => {
    const { getByRole } = render(
      <Select value="beta" onValueChange={() => {}}>
        <SelectTrigger>
          <SelectValue placeholder="Pick one" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="alpha">Alpha</SelectItem>
          <SelectItem value="beta">Beta</SelectItem>
        </SelectContent>
      </Select>,
    );
    // The label only reaches the trigger through the registry, so this failing means the
    // registration never settled rather than that the styling moved.
    expect(getByRole("button").textContent).toContain("Beta");
  });

  it("settles within a render budget instead of re-firing per registration", () => {
    let renders = 0;
    render(
      <Select value="alpha" onValueChange={() => {}}>
        <SelectTrigger>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <Counting onRender={() => renders++}>Alpha</Counting>
          <Counting onRender={() => renders++}>Beta</Counting>
          <Counting onRender={() => renders++}>Gamma</Counting>
        </SelectContent>
      </Select>,
    );
    expect(renders).toBeLessThan(12);
  });

  it("re-registers a renamed label without restarting the cycle", () => {
    function Host({ betaLabel }: { betaLabel: string }) {
      return (
        <Select value="beta" onValueChange={() => {}}>
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="alpha">Alpha</SelectItem>
            <SelectItem value="beta">{betaLabel}</SelectItem>
          </SelectContent>
        </Select>
      );
    }
    const { rerender, getByRole } = render(<Host betaLabel="Beta" />);
    expect(getByRole("button").textContent).toContain("Beta");
    // A changed label must still re-register (the trigger has to follow it) and must
    // still settle, which is the half a `useRef` guard would have broken.
    expect(() => act(() => rerender(<Host betaLabel="Second" />))).not.toThrow();
    expect(getByRole("button").textContent).toContain("Second");
  });
});
