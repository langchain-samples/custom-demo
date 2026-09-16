// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DashboardPane, type ArtifactState } from "./DashboardPane";

// The artifact panes reach for iframes, Hub routes and a markdown renderer, none of which
// this file is about: what it pins is the TAB STRIP, which is where close and collapse
// live and where a mistake is invisible until a demo.
vi.mock("./HtmlArtifact", () => ({
  HtmlArtifact: ({ path }: { path: string }) => <div data-testid="html">{path}</div>,
}));
vi.mock("./MarkdownArtifact", () => ({
  MarkdownArtifact: ({ path }: { path: string }) => <div data-testid="md">{path}</div>,
}));
vi.mock("./DashboardCanvas", () => ({ DashboardCanvas: () => <div data-testid="canvas" /> }));

const DIR = "/workspace/artifacts/";

function artifacts(...paths: string[]): Record<string, ArtifactState> {
  return Object.fromEntries(paths.map((p) => [p, { content: "# doc", streaming: false }]));
}

function setup(paths: string[], onCloseArtifact = vi.fn()) {
  render(
    <DashboardPane
      widgets={[]}
      theme="light"
      artifacts={artifacts(...paths)}
      onCloseArtifact={onCloseArtifact}
    />,
  );
  return onCloseArtifact;
}

const tabNames = () =>
  screen.getAllByRole("tab").map((t) => t.textContent?.replace(/\s+/g, " ").trim() ?? "");

afterEach(cleanup);

describe("tab strip", () => {
  it("bands a folder's documents under one clickable label", () => {
    setup([`${DIR}req-0142/brief.md`, `${DIR}req-0142/spec.md`]);
    const label = screen.getByRole("button", { name: "req-0142" });
    expect(label.getAttribute("aria-expanded")).toBe("true");
    expect(label.getAttribute("title")).toBe("Hide the documents in req-0142");
    expect(tabNames()).toHaveLength(2);
  });

  it("folds the group away when its label is clicked, and back again", () => {
    setup([`${DIR}req-0142/brief.md`, `${DIR}req-0142/spec.md`]);
    act(() => screen.getByRole("button", { name: "req-0142" }).click());

    // The tabs are gone but the group is not: the label carries the count, so the
    // documents read as folded rather than lost.
    expect(screen.queryAllByRole("tab")).toHaveLength(0);
    const collapsed = screen.getByRole("button", { name: "req-0142 (2)" });
    expect(collapsed.getAttribute("aria-expanded")).toBe("false");
    expect(collapsed.getAttribute("title")).toBe("Show the 2 documents in req-0142");

    act(() => collapsed.click());
    expect(tabNames()).toHaveLength(2);
  });

  it("keeps a document readable when another group is folded", () => {
    setup([`${DIR}req-0142/brief.md`, `${DIR}req-0311/other.md`]);
    act(() => screen.getByRole("button", { name: "req-0311" }).click());
    // req-0311 was the focused tab (last seen wins), so folding it has to move the
    // selection rather than leave the pane showing a document with no tab.
    expect(screen.getByTestId("md").textContent).toBe(`${DIR}req-0142/brief.md`);
  });

  it("closes one tab without touching the others", () => {
    const onClose = setup([`${DIR}a.md`, `${DIR}b.md`]);
    act(() => screen.getByRole("button", { name: "Close a.md" }).click());
    expect(onClose).toHaveBeenCalledWith(`${DIR}a.md`);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("says the close control keeps the document", () => {
    setup([`${DIR}a.md`]);
    // The wording is load-bearing: this button must not read as deleting a document
    // that has revision history behind it.
    const close = screen.getByRole("button", { name: "Close a.md" });
    expect(close.getAttribute("title")).toBe("Close this tab. The document is kept.");
  });

  it("routes markdown to the editor and html to the iframe", () => {
    cleanup();
    setup([`${DIR}report.html`]);
    expect(screen.getByTestId("html")).toBeTruthy();
    cleanup();
    setup([`${DIR}brief.md`]);
    expect(screen.getByTestId("md")).toBeTruthy();
  });
});
