/**
 * Rebalance: move a household's sleeves and see the trades that would result.
 *
 * The reference app for this bundle, and the one the other three copy. It takes
 * the two props in appProps.ts and nothing else: `data` is the tool result's
 * `structuredContent`, `callTool` is the only way anything leaves the iframe.
 *
 * Every class name here is styled by apps/shell.css or by the `<style>` block
 * below, and the block is part of the component so an app is one file.
 */
import { useMemo, useState } from "react";

import type { AppProps } from "./appProps";

/** One asset class in the portfolio, as the server describes it. */
interface Sleeve {
  /** Stable identifier, and the key the submitted allocation is posted under. */
  key: string;
  label: string;
  /** What the account holds today, in percent. */
  weight: number;
  /** What the investment policy says it should hold, in percent. */
  target: number;
  /** Fraction of the sleeve's value that is unrealised gain, for the tax line. */
  gain: number;
}

/** Everything the app draws, read out of the tool result. */
interface Model {
  accountId: string;
  household: string;
  portfolioValue: number;
  taxRate: number;
  sleeves: Sleeve[];
}

/** A single buy or sell implied by the current slider positions. */
interface Trade {
  sleeve: string;
  side: "BUY" | "SELL";
  amount: number;
  tax: number;
}

/**
 * Narrow the wire data into the numbers this app can compute with.
 *
 * Defaults rather than exceptions here: a portfolio with no sleeves draws an
 * empty, unsubmittable app, which is legible. Coercing every field through
 * `Number` is what keeps a string "42" from silently becoming "4242" when it
 * meets a `+`.
 */
function readModel(data: AppProps["data"]): Model {
  const raw = Array.isArray(data.sleeves) ? (data.sleeves as Record<string, unknown>[]) : [];
  return {
    accountId: String(data.account_id ?? ""),
    household: String(data.household ?? ""),
    portfolioValue: Number(data.portfolio_value ?? 0),
    taxRate: Number(data.tax_rate ?? 0),
    sleeves: raw.map((s) => ({
      key: String(s.key ?? ""),
      label: String(s.label ?? ""),
      weight: Number(s.weight ?? 0),
      target: Number(s.target ?? 0),
      gain: Number(s.unrealized_gain_pct ?? 0),
    })),
  };
}

const money = (n: number): string =>
  (n < 0 ? "-$" : "$") + Math.abs(Math.round(n)).toLocaleString();

const pct = (n: number): string => `${n.toFixed(1)}%`;

/** How far off policy a sleeve sits, as one of three bands shell.css colours. */
function driftClass(d: number): string {
  if (Math.abs(d) < 0.5) return "ok";
  return d > 0 ? "over" : "under";
}

/**
 * The app.
 *
 * Derived numbers are computed wholesale from the slider positions on every
 * render rather than patched one at a time, because drift, tax and the trade
 * list all depend on every slider at once and a partial update is how those
 * fall out of step. Height needs no attention: `useApp` in index.tsx turns on
 * the SDK's ResizeObserver, so the frame follows the content.
 */
export function Rebalance({ data, callTool }: AppProps) {
  const model = useMemo(() => readModel(data), [data]);
  const [weights, setWeights] = useState<number[]>(() => model.sleeves.map((s) => s.weight));
  const [msg, setMsg] = useState<string>(
    model.household ? `${model.household} (${model.accountId})` : "Adjust the target allocation.",
  );
  const [submitting, setSubmitting] = useState(false);

  const total = weights.reduce((a, b) => a + b, 0);
  const drift = model.sleeves.reduce((a, s, i) => a + Math.abs(weights[i] - s.target), 0);

  const trades: Trade[] = [];
  model.sleeves.forEach((s, i) => {
    const delta = weights[i] - s.weight;
    if (Math.abs(delta) < 0.05) return;

    trades.push({
      sleeve: s.label,
      side: delta > 0 ? "BUY" : "SELL",
      amount: Math.abs((delta / 100) * model.portfolioValue),
      // Only a sale realises a gain, so only a sale carries tax here.
      tax: delta < 0 ? (Math.abs(delta) / 100) * model.portfolioValue * s.gain * model.taxRate : 0,
    });
  });
  const tax = trades.reduce((a, t) => a + t.tax, 0);

  // The one hard rule: an allocation that does not sum to 100 is not a
  // portfolio, so it cannot be submitted.
  const off = Math.abs(total - 100) > 0.05;
  const blocked = off || trades.length === 0 || submitting;

  const setWeight = (i: number, value: number) =>
    setWeights((prev) => prev.map((w, j) => (j === i ? value : w)));

  async function submit() {
    if (blocked) return;

    setSubmitting(true);
    const allocation: Record<string, number> = {};
    model.sleeves.forEach((s, i) => {
      allocation[s.key] = Number(weights[i].toFixed(2));
    });
    try {
      // `submit_rebalance` is `visibility: ["app"]` on the server, so this app
      // is the only caller it has. Nesting is fine here: this is an ordinary
      // tool call, not an elicitation answer restricted to primitives.
      const res = await callTool("submit_rebalance", {
        account_id: model.accountId,
        allocation,
      });
      const out = (res.structuredContent ?? {}) as { error?: string; trade_count?: number };
      if (out.error) {
        setMsg(out.error);
        setSubmitting(false);
        return;
      }

      setMsg(`Submitted ${out.trade_count ?? 0} trades for ${model.accountId}.`);
    } catch (err) {
      // Say what failed. A submission that vanishes leaves the person believing
      // trades were placed.
      setMsg(`Could not submit: ${err instanceof Error ? err.message : String(err)}`);
      setSubmitting(false);
    }
  }

  return (
    <>
      <style>{CSS}</style>

      <p className="msg" id="msg">
        {msg}
      </p>

      <div id="sleeves">
        {model.sleeves.map((s, i) => {
          const vsPolicy = weights[i] - s.target;
          return (
            <div className="sleeve" key={s.key}>
              <div className="name">
                {s.label}
                <small>policy {pct(s.target)}</small>
              </div>
              <input
                type="range"
                min="0"
                max="100"
                step="0.5"
                value={weights[i]}
                aria-label={`${s.label} allocation`}
                onChange={(e) => setWeight(i, Number(e.target.value))}
              />
              <div className="figures">
                <span className="num">{pct(weights[i])}</span>
                <span className={`drift ${driftClass(vsPolicy)}`}>
                  {vsPolicy >= 0 ? "+" : ""}
                  {vsPolicy.toFixed(1)} vs policy
                </span>
              </div>
            </div>
          );
        })}
      </div>

      <div className="totals">
        <span>
          Allocated{" "}
          <b id="total" className={off ? "num off" : "num"}>
            {pct(total)}
          </b>
        </span>
        <span className="muted">
          Drift from policy <b className="num">{drift.toFixed(1)}</b>
        </span>
        <span className="muted">
          Est. tax <b className="num">{money(tax)}</b>
        </span>
      </div>

      {off && (
        <div className="warn" style={{ marginTop: 8 }}>
          Allocation is {pct(total)}. It has to total 100% before it can be approved.
        </div>
      )}

      <div className="card" style={{ marginTop: 10 }}>
        <span className="lbl">Resulting trades</span>
        <table id="trades">
          <tbody>
            {trades.map((t) => (
              <tr key={t.sleeve}>
                <td>{t.sleeve}</td>
                <td className={t.side === "BUY" ? "buy" : "sell"}>{t.side}</td>
                <td className="n">{money(t.amount)}</td>
                <td className="n muted">{t.tax ? `${money(t.tax)} tax` : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {trades.length === 0 && (
          <div className="muted" style={{ fontSize: "11.5px" }}>
            No change from the current allocation.
          </div>
        )}
      </div>

      <div className="row">
        <button type="button" className="primary" id="submit" disabled={blocked} onClick={submit}>
          {trades.length ? `Approve ${trades.length} trades` : "No trades to approve"}
        </button>
        <button
          type="button"
          id="reset-policy"
          onClick={() => setWeights(model.sleeves.map((s) => s.target))}
        >
          Reset to policy
        </button>
        {/* Reset, not Cancel. Nothing is waiting on this app: the tool call
            finished before the app was rendered, so there is no pause to back
            out of. Putting the sliders back where the portfolio actually sits
            is the useful action. */}
        <button
          type="button"
          id="reset-current"
          onClick={() => setWeights(model.sleeves.map((s) => s.weight))}
        >
          Reset to current
        </button>
      </div>
    </>
  );
}

/** Styles for this app alone. apps/shell.css supplies everything shared. */
const CSS = `
  .sleeve { display: grid; grid-template-columns: 92px 1fr 92px; align-items: center; gap: 8px; margin-bottom: 7px; }
  .sleeve .name { font-size: 12px; font-weight: 500; }
  .sleeve .name small { display: block; font-weight: 400; font-size: 10px; color: var(--muted); }
  .figures { text-align: right; font-size: 11.5px; font-variant-numeric: tabular-nums; }
  .figures .drift { font-size: 10px; }
  .drift.over { color: var(--down); }
  .drift.under { color: var(--up); }
  .drift.ok { color: var(--muted); }
  .totals { display: flex; gap: 14px; align-items: baseline; margin-top: 10px; font-size: 11.5px; }
  .totals b { font-size: 13px; font-variant-numeric: tabular-nums; }
  .off { color: var(--down); }
  #trades td { border-top: 1px solid var(--border); font-size: 11.5px; }
  .buy { color: var(--up); }
  .sell { color: var(--down); }
`;
