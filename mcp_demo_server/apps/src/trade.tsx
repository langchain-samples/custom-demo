/**
 * Trade: a single order ticket, priced and checked before it is sent.
 *
 * Takes the two props in appProps.ts and nothing else: `data` is the quote and
 * position the ticket is drawn from, `callTool` is the only way the order
 * leaves the iframe. rebalance.tsx is the shape every app in this bundle
 * follows.
 *
 * The gesture is the point. Placing an order is irreversible, so the confirm
 * button has to be held down for HOLD_MS while a fill sweeps across it, and
 * that hold is what authorises `submit_trade`. Every class name here is styled
 * by apps/shell.css or by the `<style>` block below, and the block is part of
 * the component so an app is one file.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent } from "react";

import type { AppProps } from "./appProps";

/**
 * How long the confirm button must be held.
 *
 * Long enough that the reflex click which dismisses a dialog cannot place an
 * order, short enough that a deliberate press does not feel broken.
 */
const HOLD_MS = 900;

/** What kind of order this is, which decides the price the estimate uses. */
type OrderType = "market" | "limit";

/** How long the order rests, in the server's own vocabulary. */
type TimeInForce = "day" | "gtc" | "ioc";

/** The quote and position the ticket is drawn from. */
interface Order {
  /** The account the order is placed in, posted back with the ticket. */
  accountId: string;
  household: string;
  side: "BUY" | "SELL";
  symbol: string;
  /** The security's display name, shown beside the ticker. */
  name: string;
  /** Last traded price, and the price a market order is estimated at. */
  last: number;
  currency: string;
  /** Flat commission, added to a purchase and netted out of a sale. */
  fee: number;
  /** Shares held, which a sale cannot exceed. Zero means uncapped. */
  maxQty: number;
  /** Quantity the ticket opens with, and the one the reset button restores. */
  defaultQty: number;
}

/**
 * Narrow the wire data into the quote this ticket prices against.
 *
 * Defaults rather than exceptions: a ticket with no quote prices at zero and
 * refuses to submit, which is legible. Coercing every number through `Number`
 * is what keeps a string "100" from concatenating where it should add. The
 * quantity falls back to 100 rather than to zero, because it is also what the
 * reset button restores and a reset to zero is a dead ticket.
 */
function readOrder(data: AppProps["data"]): Order {
  const side = String(data.side ?? "BUY").toUpperCase() === "SELL" ? "SELL" : "BUY";
  return {
    accountId: String(data.account_id ?? ""),
    household: String(data.household ?? ""),
    side,
    symbol: String(data.symbol ?? "-"),
    name: String(data.name ?? ""),
    last: Number(data.last ?? 0),
    currency: String(data.currency ?? "$"),
    fee: Number(data.fee ?? 0),
    maxQty: Number(data.max_qty ?? 0),
    defaultQty: Number(data.default_qty ?? 0) || 100,
  };
}

/**
 * The app.
 *
 * The three inputs are held as strings, the way the fields hold them, so an
 * emptied quantity box reads as an invalid ticket instead of as NaN. Every
 * estimate and the one blocking problem are recomputed from those strings on
 * each render rather than patched field by field, because the price, the
 * principal, the total and the disabled state all depend on all of them at
 * once. Height needs no attention: `useApp` in index.tsx turns on the SDK's
 * ResizeObserver, so the frame follows the content.
 */
export function Trade({ data, callTool }: AppProps) {
  const order = useMemo(() => readOrder(data), [data]);
  const [qty, setQty] = useState<string>(() => String(order.defaultQty));
  const [orderType, setOrderType] = useState<OrderType>("market");
  const [limit, setLimit] = useState<string>(() => (order.last ? order.last.toFixed(2) : ""));
  const [tif, setTif] = useState<TimeInForce>("day");
  const [msg, setMsg] = useState<string>(order.household || "Review the order.");
  const [holding, setHolding] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const money = (n: number): string =>
    order.currency +
    n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  const quantity = Number(qty) || 0;
  const price = orderType === "limit" ? Number(limit) || 0 : order.last;
  const principal = quantity * price;
  // A sale returns proceeds net of the fee; a purchase costs principal plus.
  const total = order.side === "SELL" ? principal - order.fee : principal + order.fee;

  const problem = ((): string => {
    if (!(Number(qty) > 0)) return "Quantity has to be at least 1.";
    if (order.maxQty && Number(qty) > order.maxQty) {
      return `Only ${order.maxQty.toLocaleString()} shares are held in this account.`;
    }

    if (orderType === "limit" && !(Number(limit) > 0)) return "Set a limit price.";
    return "";
  })();
  const blocked = problem !== "" || submitting;

  const label = submitting ? "Submitting..." : holding ? "Keep holding..." : "Hold to submit";

  /* ---- hold to confirm ---- */

  // The fill is written straight to the DOM from inside the animation frame.
  // Sixty renders a second would put the ResizeObserver to work on every one of
  // them for a bar that no other part of the app reads, and React leaves the
  // element's inline style alone because no `style` prop is passed to it.
  const fillRef = useRef<HTMLElement | null>(null);
  const frameRef = useRef<number | null>(null);
  const startRef = useRef(0);

  // The frame loop outlives the render that scheduled it, so it reaches the
  // submit through a ref that every render refreshes. Reading `place` out of
  // the closure instead would price and post whatever the ticket held when the
  // hold began.
  const placeRef = useRef<() => void>(() => {});

  function endHold() {
    if (frameRef.current === null) return;

    cancelAnimationFrame(frameRef.current);
    frameRef.current = null;
    if (fillRef.current) fillRef.current.style.width = "0%";
    setHolding(false);
  }

  function tick() {
    const progress = Math.min(1, (Date.now() - startRef.current) / HOLD_MS);
    if (fillRef.current) fillRef.current.style.width = `${progress * 100}%`;
    if (progress >= 1) {
      placeRef.current();
      return;
    }

    frameRef.current = requestAnimationFrame(tick);
  }

  function beginHold(event: PointerEvent<HTMLButtonElement>) {
    if (blocked) return;

    // Keep the press from turning into a text selection or a drag partway
    // through the hold, which would end it without the person letting go.
    event.preventDefault();
    startRef.current = Date.now();
    setHolding(true);
    frameRef.current = requestAnimationFrame(tick);
  }

  async function place() {
    endHold();
    setSubmitting(true);
    try {
      // `submit_trade` is `visibility: ["app"]` on the server, and here that
      // matters most: the hold IS the authorisation for a real order, so the
      // model must not be able to claim it happened.
      const res = await callTool("submit_trade", {
        account_id: order.accountId,
        symbol: order.symbol,
        side: order.side.toLowerCase(),
        ticket: {
          quantity: Number(qty),
          order_type: orderType,
          limit_price: orderType === "limit" ? Number(limit) : null,
          time_in_force: tif,
          confirmed: true,
        },
      });
      const out = (res.structuredContent ?? {}) as {
        error?: string;
        order_id?: string;
        status?: string;
      };
      if (out.error) {
        setMsg(out.error);
        setSubmitting(false);
        return;
      }

      setMsg(`Order ${out.order_id} ${out.status}.`);
    } catch (err) {
      // Say what failed, and let go of the button. An order that vanishes
      // leaves the person believing it was placed.
      setMsg(`Could not place: ${err instanceof Error ? err.message : String(err)}`);
      setSubmitting(false);
    }
  }

  useEffect(() => {
    placeRef.current = place;
  });

  useEffect(() => {
    return () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    };
  }, []);

  return (
    <>
      <style>{CSS}</style>

      <p className="msg" id="msg">
        {msg}
      </p>

      <div className="card" style={{ marginBottom: 10 }}>
        <div className="est">
          <span>
            <span className={`side ${order.side === "SELL" ? "sell" : "buy"}`}>
              {order.side}
            </span>{" "}
            <b>{order.symbol}</b> <span className="muted">{order.name}</span>
          </span>
          <span className="muted num">{order.last ? `last ${money(order.last)}` : ""}</span>
        </div>
      </div>

      <div className="ticket">
        <div className="field">
          <span className="lbl">Quantity</span>
          <div className="stepper">
            <button
              type="button"
              aria-label="Decrease quantity"
              onClick={() => setQty(String(Math.max(1, Number(qty) - 10)))}
            >
              -
            </button>
            <input
              type="number"
              id="qty"
              min="1"
              step="1"
              value={qty}
              aria-label="Quantity"
              onChange={(e) => setQty(e.target.value)}
            />
            <button
              type="button"
              aria-label="Increase quantity"
              onClick={() => setQty(String(Number(qty) + 10))}
            >
              +
            </button>
          </div>
        </div>
        <div className="field">
          <span className="lbl">Order type</span>
          <div className="seg">
            <label>
              <input
                type="radio"
                name="ot"
                value="market"
                checked={orderType === "market"}
                onChange={() => setOrderType("market")}
              />
              <span>Market</span>
            </label>
            <label>
              <input
                type="radio"
                name="ot"
                value="limit"
                checked={orderType === "limit"}
                onChange={() => setOrderType("limit")}
              />
              <span>Limit</span>
            </label>
          </div>
        </div>
        {orderType === "limit" && (
          <div className="field">
            <span className="lbl">Limit price</span>
            <input
              type="number"
              id="limit"
              step="0.01"
              value={limit}
              aria-label="Limit price"
              onChange={(e) => setLimit(e.target.value)}
            />
          </div>
        )}
        <div className="field">
          <span className="lbl">Time in force</span>
          <select
            id="tif"
            value={tif}
            aria-label="Time in force"
            onChange={(e) => setTif(e.target.value as TimeInForce)}
          >
            <option value="day">Day</option>
            <option value="gtc">Good til cancelled</option>
            <option value="ioc">Immediate or cancel</option>
          </select>
        </div>
      </div>

      <div className="card" style={{ marginTop: 10 }}>
        <div className="est">
          <span className="muted">Estimated principal</span>
          <b>{money(principal)}</b>
        </div>
        <div className="est">
          <span className="muted">Commission</span>
          <b>{money(order.fee)}</b>
        </div>
        <div
          className="est"
          style={{ borderTop: "1px solid var(--border)", marginTop: 3, paddingTop: 5 }}
        >
          <span>Estimated total</span>
          <b id="total">{money(total)}</b>
        </div>
      </div>

      {problem && (
        <div className="warn" style={{ marginTop: 8 }}>
          {problem}
        </div>
      )}

      <div className="row">
        <button
          type="button"
          className="primary"
          id="confirm"
          disabled={blocked}
          onPointerDown={beginHold}
          onPointerUp={endHold}
          onPointerLeave={endHold}
          onPointerCancel={endHold}
        >
          <span>{label}</span>
          <i id="fill" ref={fillRef} />
        </button>
        {/* Reset, not Cancel. Nothing is waiting on this app: the tool call
            finished before the ticket was rendered, so there is no pause to
            back out of. Putting the quantity back to the one the ticket opened
            with is the useful action. */}
        <button type="button" id="cancel" onClick={() => setQty(String(order.defaultQty))}>
          Reset quantity
        </button>
      </div>
      <div className="muted" style={{ fontSize: "10.5px", marginTop: 6 }}>
        Press and hold to place the order. Estimates exclude taxes and are not a quote.
      </div>
    </>
  );
}

/** Styles for this app alone. apps/shell.css supplies everything shared. */
const CSS = `
  .ticket { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .field { display: flex; flex-direction: column; gap: 3px; }
  .stepper { display: flex; align-items: stretch; gap: 0; }
  .stepper button { border-radius: 8px 0 0 8px; width: 30px; padding: 0; font-size: 15px; }
  .stepper button:last-child { border-radius: 0 8px 8px 0; }
  .stepper input { border-radius: 0; text-align: center; border-left: 0; border-right: 0; }
  .seg { display: flex; gap: 0; }
  .seg label {
    flex: 1; text-align: center; padding: 6px 0; font-size: 12px; cursor: pointer;
    border: 1px solid var(--border); background: transparent;
  }
  .seg label:first-child { border-radius: 8px 0 0 8px; }
  .seg label:last-child { border-radius: 0 8px 8px 0; border-left: 0; }
  .seg input { position: absolute; opacity: 0; pointer-events: none; }
  .seg input:checked + span { font-weight: 600; }
  .seg label:has(input:checked) { background: var(--accent); border-color: var(--accent); color: var(--accent-fg); }
  .est { display: flex; justify-content: space-between; font-size: 12px; padding: 3px 0; }
  .est b { font-variant-numeric: tabular-nums; }
  .side { font-weight: 700; letter-spacing: 0.04em; }
  .side.buy { color: var(--up); }
  .side.sell { color: var(--down); }
  /* The hold-to-confirm fill, which is the whole point of this app. */
  #confirm { position: relative; overflow: hidden; }
  #fill {
    position: absolute; inset: 0; width: 0%;
    background: rgb(255 255 255 / 0.28); pointer-events: none;
  }
  #confirm span { position: relative; }
`;
