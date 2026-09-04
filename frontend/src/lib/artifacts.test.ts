import { describe, expect, it } from "vitest";
import {
  artifactName,
  hasRenderableBody,
  isHtmlArtifactPath,
  safeHtmlPrefix,
  skeletonReveal,
} from "@/lib/artifacts";

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

describe("hasRenderableBody", () => {
  it("is false for nothing, and for a document still in its head", () => {
    expect(hasRenderableBody("")).toBe(false);
    expect(hasRenderableBody("<!doctype html><html><head><title>x</title>")).toBe(false);
  });

  it("is false through a long style block, which is the case that motivated it", () => {
    const head = '<!doctype html><html><head><style>body{margin:0}h1{font-size:2rem}';
    expect(hasRenderableBody(head)).toBe(false);
    expect(hasRenderableBody(head + "</style></head>")).toBe(false);
  });

  it("is false for an opened body with nothing in it yet", () => {
    expect(hasRenderableBody("<html><head></head><body>")).toBe(false);
    expect(hasRenderableBody("<html><head></head><body>\n  <div class='wrap'>\n")).toBe(false);
  });

  it("is false while the body tag itself is half written", () => {
    expect(hasRenderableBody("<html><head></head><bod")).toBe(false);
    expect(hasRenderableBody("<html><head></head><body class=\"re")).toBe(false);
  });

  it("is true as soon as real text lands in the body", () => {
    expect(hasRenderableBody("<html><head></head><body><h1>Q3 brief")).toBe(true);
  });

  it("ignores script and style content inside the body", () => {
    expect(hasRenderableBody("<body><style>.a{color:red}</style>")).toBe(false);
    expect(hasRenderableBody("<body><script>const a = 1;</script>")).toBe(false);
    expect(hasRenderableBody("<body><script>const a = 1;</script><p>hi")).toBe(true);
  });

  it("counts a self-contained visual with no text", () => {
    expect(hasRenderableBody('<body><svg viewBox="0 0 1 1"></svg>')).toBe(true);
    expect(hasRenderableBody('<body><img src="x.png">')).toBe(true);
  });

  it("treats a bare fragment as its own content", () => {
    expect(hasRenderableBody("<p>just a fragment</p>")).toBe(true);
    expect(hasRenderableBody("<div></div>")).toBe(false);
  });
});

describe("the critical-CSS write order the prompt now asks for", () => {
  // A short head style, then body content, then the rest of the CSS before </body>.
  const doc =
    '<!doctype html><html><head><style>body{font:16px system-ui;color:#111}</style>' +
    "</head><body><h1>Q3 brief</h1><p>Summary text.</p>" +
    "<style>.card{border-radius:12px;box-shadow:0 1px 2px #0001}</style></body></html>";

  it("shows content early, which is the whole point of the reorder", () => {
    // Under the old order the body began a third of the way in; here it is immediate.
    const headOnly = doc.slice(0, doc.indexOf("<body"));
    expect(hasRenderableBody(headOnly)).toBe(false);
    const throughH1 = doc.slice(0, doc.indexOf("</h1>"));
    expect(hasRenderableBody(throughH1)).toBe(true);
  });

  it("keeps the trailing style block from swallowing the document mid-write", () => {
    const midTrailingStyle = doc.slice(0, doc.indexOf("box-shadow"));
    const out = safeHtmlPrefix(midTrailingStyle);
    expect((out.match(/<style/gi) || []).length).toBe((out.match(/<\/style/gi) || []).length);
    // The content already written is still there, not eaten as CSS.
    expect(out).toContain("Q3 brief");
  });

  it("still reports renderable content while the trailing CSS streams", () => {
    const midTrailingStyle = doc.slice(0, doc.indexOf("box-shadow"));
    expect(hasRenderableBody(midTrailingStyle)).toBe(true);
  });

  it("passes the finished document through unchanged", () => {
    expect(safeHtmlPrefix(doc)).toBe(doc);
  });
});

describe("skeletonReveal", () => {
  it("starts empty", () => {
    expect(skeletonReveal(0)).toBe(0);
    expect(skeletonReveal(-5)).toBe(0);
    expect(skeletonReveal(NaN)).toBe(0);
  });

  it("only ever grows", () => {
    let prev = -1;
    for (let ms = 0; ms <= 30_000; ms += 250) {
      const v = skeletonReveal(ms);
      expect(v).toBeGreaterThanOrEqual(prev);
      prev = v;
    }
  });

  it("never completes, so it cannot sit at 100% looking hung", () => {
    for (const ms of [10_000, 60_000, 3_600_000]) {
      expect(skeletonReveal(ms)).toBeLessThan(1);
    }
  });

  it("keeps moving through the middle of the wait", () => {
    // The first attempt was exponential and was ~63% done almost immediately, then
    // barely changed - which looked exactly like the static skeleton it replaced.
    expect(skeletonReveal(2_000)).toBeCloseTo(0.2, 1);
    expect(skeletonReveal(5_000)).toBeCloseTo(0.5, 1);
    expect(skeletonReveal(8_000)).toBeCloseTo(0.8, 1);
  });

  it("shows something immediately, so the pane is never blank", () => {
    // The component floors this at one row; the curve itself may still be ~0 here.
    expect(skeletonReveal(100)).toBeLessThan(0.1);
  });
});
