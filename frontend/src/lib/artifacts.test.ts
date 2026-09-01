import { describe, expect, it } from "vitest";
import { artifactName, isHtmlArtifactPath, safeHtmlPrefix } from "@/lib/artifacts";

describe("isHtmlArtifactPath", () => {
  it("accepts html and htm under the artifact dir", () => {
    expect(isHtmlArtifactPath("/workspace/artifacts/report.html")).toBe(true);
    expect(isHtmlArtifactPath("/workspace/artifacts/deep/report.htm")).toBe(true);
  });

  it("rejects other directories, other extensions and nothing", () => {
    expect(isHtmlArtifactPath("/workspace/data/report.html")).toBe(false);
    expect(isHtmlArtifactPath("/workspace/artifacts/notes.md")).toBe(false);
    expect(isHtmlArtifactPath(undefined)).toBe(false);
  });
});

describe("artifactName", () => {
  it("is the basename", () => {
    expect(artifactName("/workspace/artifacts/q3-report.html")).toBe("q3-report.html");
  });
});

describe("safeHtmlPrefix", () => {
  it("passes a complete document through untouched", () => {
    const html = "<h1>Title</h1><p>Body</p>";
    expect(safeHtmlPrefix(html)).toBe(html);
  });

  it("is empty for empty input", () => {
    expect(safeHtmlPrefix("")).toBe("");
  });

  it("leaves unclosed block elements alone (the parser implies their end tags)", () => {
    expect(safeHtmlPrefix("<div><p>hello")).toBe("<div><p>hello");
  });

  it("drops a half-written tag rather than showing its source", () => {
    expect(safeHtmlPrefix('<h1>Title</h1><div class="ca')).toBe("<h1>Title</h1>");
    expect(safeHtmlPrefix("<p>done</p><")).toBe("<p>done</p>");
  });

  it("closes an unclosed style so the CSS written so far applies", () => {
    expect(safeHtmlPrefix("<style>body { color: red; }")).toBe(
      "<style>body { color: red; }</style>",
    );
  });

  it("leaves a closed style alone", () => {
    const html = "<style>body{color:red}</style><p>hi</p>";
    expect(safeHtmlPrefix(html)).toBe(html);
  });

  it("empties an unclosed script so a half statement cannot throw", () => {
    expect(safeHtmlPrefix('<script>const a = fetch("/x' )).toBe("<script></script>");
    expect(safeHtmlPrefix("<p>hi</p><script>\nlet x = 1;\nlet y =")).toBe(
      "<p>hi</p><script></script>",
    );
  });

  it("does not truncate at a less-than inside script source", () => {
    // The trailing-tag trim would cut at `<` in `a < b` if raw text ran second.
    expect(safeHtmlPrefix("<script>if (a < b) { go(); }")).toBe("<script></script>");
  });

  it("keeps a completed script and its content", () => {
    const html = "<script>go();</script><p>after</p>";
    expect(safeHtmlPrefix(html)).toBe(html);
  });

  it("handles a script whose opening tag is itself half written", () => {
    expect(safeHtmlPrefix("<p>hi</p><scri")).toBe("<p>hi</p>");
  });

  it("does not treat a longer element name as script or style", () => {
    const html = "<scriptish>text</scriptish>";
    expect(safeHtmlPrefix(html)).toBe(html);
  });

  it("drops an unterminated comment, which would hide the rest", () => {
    expect(safeHtmlPrefix("<p>shown</p><!-- note in progr")).toBe("<p>shown</p>");
  });

  it("keeps a terminated comment", () => {
    const html = "<p>a</p><!-- note --><p>b</p>";
    expect(safeHtmlPrefix(html)).toBe(html);
  });

  it("grows monotonically as a document streams in", () => {
    const full = '<style>p{color:red}</style><h1>T</h1><script>run();</script><p>end</p>';
    let previous = "";
    for (let i = 1; i <= full.length; i++) {
      const out = safeHtmlPrefix(full.slice(0, i));
      // Never emit something longer than the source plus one synthetic closing tag.
      expect(out.length).toBeLessThanOrEqual(full.length + "</script>".length);
      previous = out;
    }
    expect(previous).toBe(full);
  });
});
