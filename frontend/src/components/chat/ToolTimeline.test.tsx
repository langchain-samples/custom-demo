// @vitest-environment jsdom
/**
 * The live tool burst: the newest calls as timeline rows, everything older folded into
 * one openable row above them.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { ToolActivityView } from "@/components/chat/ToolTimeline";
import type { ChipData } from "@/components/chat/ToolChip";

afterEach(cleanup);

function chip(id: string, name: string, result: string | null = "ok"): ChipData {
  return { id, name, arg: "", result };
}

describe("the live tool timeline", () => {
  it("shows a short burst entirely as timeline rows", () => {
    const { container, queryByText } = render(
      <ToolActivityView chips={[chip("a", "ls"), chip("b", "grep")]} />,
    );
    expect(container.querySelectorAll("li")).toHaveLength(2);
    expect(queryByText(/\+\d+ step/)).toBeNull();
  });

  it("keeps the newest three as rows and folds the rest into one row", () => {
    const chips = [
      chip("a", "ls"),
      chip("b", "glob"),
      chip("c", "read_file"),
      chip("d", "grep"),
      chip("e", "execute", null),
    ];
    const { container, getByText } = render(<ToolActivityView chips={chips} />);
    const rows = Array.from(container.querySelectorAll("li"), (li) => li.textContent);
    expect(rows).toEqual(["Read a file", "Searched files", "Running a command…"]);
    // The folded row is titled by its newest call and counts the one behind it.
    expect(getByText("Found files")).toBeTruthy();
    expect(getByText("+1 step")).toBeTruthy();
  });

  it("marks the in-flight call with the running dots", () => {
    const { getAllByRole } = render(<ToolActivityView chips={[chip("a", "execute", null)]} />);
    expect(getAllByRole("status")).toHaveLength(1);
  });
});
