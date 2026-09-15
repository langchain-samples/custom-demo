/**
 * Signature: the document a client signs without leaving the conversation.
 *
 * Takes the two props in appProps.ts: `data` names the document being signed,
 * `callTool` is how the drawn signature leaves the iframe.
 *
 * THE PAD IS IMPERATIVE ON PURPOSE. A stroke is pixels on a <canvas>, and the
 * canvas is the only piece of this app React does not own: the backing store,
 * the 2D context and the extent of the ink all live in refs, and React is told
 * one thing about them (`inked`, which is what enables Confirm and hides the
 * hint). Keeping the stroke out of state is not an optimisation, it is
 * correctness: re-rendering does not repaint a canvas, so a stroke held in
 * state would be a stroke that disappears the next time anything else changes.
 *
 * Every class name here is styled by apps/shell.css or by the `<style>` block
 * below, and the block is part of the component so an app is one file.
 */
import { useLayoutEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";

import type { AppProps } from "./appProps";

/** What the pad labels itself with, read out of the tool result. */
interface DocumentInfo {
  accountId: string;
  household: string;
  /** What is being signed, e.g. "IPS amendment". */
  name: string;
}

/**
 * Narrow the wire data into the three strings this app puts on screen and posts
 * back. Defaults rather than exceptions: a pad that cannot name its document
 * still collects a valid signature, it just says "Sign to confirm."
 */
function readDocument(data: AppProps["data"]): DocumentInfo {
  return {
    accountId: String(data.account_id ?? ""),
    household: String(data.household ?? ""),
    name: String(data.document ?? ""),
  };
}

/** The drawn extent, as the two corners of a box in CSS pixels. */
interface Ink {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

/** A rectangle on the pad, in CSS pixels. */
interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** One position on the pad, in CSS pixels. */
interface Point {
  x: number;
  y: number;
}

/**
 * The budget the result has to fit in, in data-URI characters.
 *
 * The server caps what it will inline (`inline_budget` in
 * mcp_demo_server/server.py), and a signature that comes back over the cap is
 * useless: the document then has nothing to show. So the app guarantees the size
 * rather than hoping, and shrinks until it fits.
 */
const BUDGET = 10000;

/** The export widths to try, largest first. The ladder never upscales. */
const WIDTHS = [480, 380, 300, 220, 160];

/**
 * Match the backing store to the CSS box at the device's pixel ratio, so
 * strokes are sharp and hit-testing lines up.
 *
 * Also the one place the drawing state is configured, because assigning
 * `width` or `height` resets the context: line settings survive only if they
 * are set after the resize.
 */
function fit(canvas: HTMLCanvasElement): void {
  const ctx = canvas.getContext("2d");
  const rect = canvas.getBoundingClientRect();
  if (!ctx || !rect.width) return;

  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(rect.width * ratio);
  canvas.height = Math.round(rect.height * ratio);
  // Every coordinate below this line is a CSS pixel, which is also what a
  // pointer event reports, so nothing has to convert.
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.lineWidth = 2;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.strokeStyle = getComputedStyle(document.body).color;
}

/** Where a pointer is, in the canvas's own CSS pixels. */
function point(canvas: HTMLCanvasElement, event: ReactPointerEvent): Point {
  const rect = canvas.getBoundingClientRect();
  return { x: event.clientX - rect.left, y: event.clientY - rect.top };
}

/** Grow the ink box to include a point, or start one at it. */
function extend(ink: Ink | null, x: number, y: number): Ink {
  if (!ink) return { x0: x, y0: y, x1: x, y1: y };

  return {
    x0: Math.min(ink.x0, x),
    y0: Math.min(ink.y0, y),
    x1: Math.max(ink.x1, x),
    y1: Math.max(ink.y1, y),
  };
}

/**
 * Flatten the stroke so PNG can actually compress it.
 *
 * A drawn line is antialiased, so every edge pixel is a slightly different RGBA
 * value and the compressor has thousands of distinct colours to encode for what
 * is visually one black line. Forcing RGB to black and snapping alpha to a few
 * steps collapses that to a handful of values, which is worth several KB, far
 * more than the resolution change, and is invisible at signature scale.
 */
function flatten(octx: CanvasRenderingContext2D, w: number, h: number): void {
  let image: ImageData;
  try {
    image = octx.getImageData(0, 0, w, h);
  } catch {
    // No pixel access, which should not happen for a canvas this app drew
    // itself. The export is still a correct signature, only larger, so it goes
    // out unflattened rather than not at all.
    return;
  }

  const d = image.data;
  for (let i = 0; i < d.length; i += 4) {
    d[i] = 0;
    d[i + 1] = 0;
    d[i + 2] = 0;
    // Five alpha steps: solid core, a couple of edge tones, or nothing.
    d[i + 3] = Math.round(d[i + 3] / 64) * 64;
  }
  octx.putImageData(image, 0, 0);
}

/** Render the cropped ink at `width` CSS pixels, as a data URI. */
function renderAt(canvas: HTMLCanvasElement, box: Box, width: number): string | null {
  const ratio = window.devicePixelRatio || 1;
  const scale = Math.min(1, width / box.w);
  const out = document.createElement("canvas");
  out.width = Math.max(1, Math.round(box.w * scale));
  out.height = Math.max(1, Math.round(box.h * scale));
  const octx = out.getContext("2d");
  if (!octx) return null;

  // Source rect is in the backing store's device pixels; the destination is the
  // CSS-scaled box, which is the downscale.
  octx.drawImage(
    canvas,
    box.x * ratio,
    box.y * ratio,
    box.w * ratio,
    box.h * ratio,
    0,
    0,
    out.width,
    out.height,
  );
  flatten(octx, out.width, out.height);
  return out.toDataURL("image/png");
}

/**
 * The signature as a compact PNG data URI.
 *
 * Cropped to the ink (plus a small margin so the stroke is not clipped),
 * flattened, and rendered at the largest width in WIDTHS that fits BUDGET.
 * Exporting the whole pad at device resolution produced a 1244x300 PNG of
 * 13.7KB for a stroke covering a fraction of it, because a signature is line
 * art and the empty margin is most of the file. Cropped, it lands in
 * single-digit KB: small enough to travel inline in the tool result and be
 * embedded straight into a document that then needs no network to render,
 * print, or outlive this server.
 */
function exportSignature(canvas: HTMLCanvasElement, ink: Ink | null): string {
  const ratio = window.devicePixelRatio || 1;
  const margin = 8;
  const w = canvas.width / ratio;
  const h = canvas.height / ratio;
  const box: Box = ink
    ? {
        x: Math.max(0, ink.x0 - margin),
        y: Math.max(0, ink.y0 - margin),
        w: Math.min(w, ink.x1 + margin) - Math.max(0, ink.x0 - margin),
        h: Math.min(h, ink.y1 + margin) - Math.max(0, ink.y0 - margin),
      }
    : { x: 0, y: 0, w, h };
  if (!(box.w > 0 && box.h > 0)) return canvas.toDataURL("image/png");

  let last: string | null = null;
  for (const width of WIDTHS) {
    const uri = renderAt(canvas, box, width);
    if (!uri) break;

    last = uri;
    if (uri.length <= BUDGET) return uri;
  }
  // Every width was still over budget, which a real signature never is. Send
  // the smallest anyway: a slightly rough image beats none.
  return last ?? canvas.toDataURL("image/png");
}

/** The stamp beside Clear: when this pad was opened, in the viewer's locale. */
const openedAt = (): string =>
  new Date().toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });

/**
 * The app.
 *
 * Height needs no attention: `useApp` in index.tsx turns on the SDK's
 * ResizeObserver, so the frame follows the content.
 */
export function Signature({ data, callTool }: AppProps) {
  const doc = useMemo(() => readDocument(data), [data]);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  /** The ink box, and whether a pointer is currently down. Neither is drawn. */
  const ink = useRef<Ink | null>(null);
  const drawing = useRef(false);
  const [inked, setInked] = useState(false);
  const [who, setWho] = useState("");
  const [msg, setMsg] = useState<string>(
    doc.name ? `${doc.name} for ${doc.household || doc.accountId}` : "Sign to confirm.",
  );
  /** True from the press of Confirm, and back to false only if it failed. */
  const [signed, setSigned] = useState(false);
  const [stamp] = useState(openedAt);

  // A layout effect, because this measures the pad: the style block above is in
  // the same commit, so the box is the styled one by the time this runs, and
  // the canvas is sized before the first paint.
  useLayoutEffect(() => {
    if (canvasRef.current) fit(canvasRef.current);
  }, []);

  // Both halves are needed: a signature nobody is named for cannot be filed,
  // and a name with no signature is not a signature.
  const blocked = !inked || !who.trim() || signed;

  function down(event: ReactPointerEvent<HTMLCanvasElement>) {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;

    drawing.current = true;
    // Capture, so a stroke that runs off the edge of the pad keeps drawing and
    // still ends on this element's own pointerup.
    canvas.setPointerCapture(event.pointerId);
    const p = point(canvas, event);
    ink.current = extend(ink.current, p.x, p.y);
    ctx.beginPath();
    ctx.moveTo(p.x, p.y);
    // A tap with no drag is still a mark, so draw a dot rather than nothing.
    ctx.lineTo(p.x + 0.01, p.y);
    ctx.stroke();
    setInked(true);
  }

  function move(event: ReactPointerEvent<HTMLCanvasElement>) {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!drawing.current || !canvas || !ctx) return;

    const p = point(canvas, event);
    ink.current = extend(ink.current, p.x, p.y);
    ctx.lineTo(p.x, p.y);
    ctx.stroke();
  }

  /**
   * End the stroke.
   *
   * On pointerup, and also on pointercancel and pointerleave: a pointer that
   * goes away without a pointerup (a cancelled gesture, or an engine that
   * refused the capture request) must not leave the pad drawing a line from
   * wherever it reappears.
   */
  function stop(event: ReactPointerEvent<HTMLCanvasElement>) {
    if (!drawing.current) return;

    drawing.current = false;
    const canvas = canvasRef.current;
    if (canvas?.hasPointerCapture(event.pointerId)) {
      canvas.releasePointerCapture(event.pointerId);
    }
  }

  function clear() {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;

    // The whole backing store, in its own device pixels. `fit` left a
    // CSS-pixel transform on the context, so the clear drops it for one call
    // and save/restore puts it back.
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.restore();
    ink.current = null;
    setInked(false);
  }

  async function submit() {
    const canvas = canvasRef.current;
    if (!canvas || blocked) return;

    setSigned(true);
    try {
      // `submit_signature` is `visibility: ["app"]` on the server, and the
      // reason is the strongest of the four apps: a wet signature is a wet
      // signature, and a model able to call this could sign for a client.
      // `capture`'s three keys are exactly `SignatureCapture` in
      // mcp_demo_server/server.py; a rename on either side fails validation
      // after the person has already drawn.
      const res = await callTool("submit_signature", {
        account_id: doc.accountId,
        document: doc.name,
        capture: {
          signature: exportSignature(canvas, ink.current),
          signed_by: who.trim(),
          signed_at: new Date().toISOString(),
        },
      });
      const out = (res.structuredContent ?? {}) as { error?: string; reference?: string };
      if (out.error) {
        setMsg(out.error);
        setSigned(false);
        return;
      }

      setMsg(out.reference ? `Signed, reference ${out.reference}.` : "Signed.");
    } catch (err) {
      // Say what failed, and let them try again. A signature that vanishes
      // leaves the person believing the document is signed.
      setMsg(`Could not sign: ${err instanceof Error ? err.message : String(err)}`);
      setSigned(false);
    }
  }

  return (
    <>
      <style>{CSS}</style>

      <p className="msg" id="msg">
        {msg}
      </p>

      <label className="lbl" htmlFor="who">
        Received by
      </label>
      <input
        id="who"
        type="text"
        autoComplete="off"
        placeholder="Full name"
        value={who}
        onChange={(e) => setWho(e.target.value)}
      />

      <span className="lbl">Signature</span>
      <div className={inked ? "pad inked" : "pad"} id="pad">
        <div className="baseline" />
        <canvas
          id="canvas"
          ref={canvasRef}
          tabIndex={0}
          aria-label="Signature pad"
          onPointerDown={down}
          onPointerMove={move}
          onPointerUp={stop}
          onPointerCancel={stop}
          onPointerLeave={stop}
        />
        <div className="hint">Draw your signature here</div>
      </div>

      <div className="row">
        {/* Clear, not Cancel. The tool call finished before this app was
            rendered, so there is nothing to back out of; redrawing is the only
            way back a pad needs. */}
        <button type="button" id="clear" onClick={clear}>
          Clear
        </button>
        <span className="spacer" />
        <span className="stamp" id="stamp">
          {stamp}
        </span>
      </div>
      <div className="row">
        <button type="button" className="primary" id="submit" disabled={blocked} onClick={submit}>
          Confirm
        </button>
      </div>
    </>
  );
}

/** Styles for this app alone. apps/shell.css supplies everything shared. */
const CSS = `
  .pad { position: relative; border: 1px dashed var(--border); border-radius: 10px; background: var(--panel); overflow: hidden; }
  canvas { display: block; width: 100%; height: 150px; touch-action: none; cursor: crosshair; }
  .hint { position: absolute; inset: 0; display: grid; place-items: center; font-size: 12px; color: var(--muted); pointer-events: none; }
  .pad.inked .hint { display: none; }
  .baseline { position: absolute; left: 14px; right: 14px; bottom: 30px; border-bottom: 1px solid var(--border); pointer-events: none; }
  #who { margin-bottom: 10px; }
  .stamp { font-size: 11px; color: var(--muted); }
`;
