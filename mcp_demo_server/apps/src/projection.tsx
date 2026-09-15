/**
 * Projection: the retirement outlook an advisor walks a household through.
 *
 * Three trackbars (retirement age, monthly contribution, risk posture) over a
 * projection band that redraws as they move, and a verdict line saying what the
 * median lands at and how likely the goal is. The whole projection is computed
 * in here, because the point of the app is that the chart follows the drag, and
 * a round trip to the server per pixel would make it feel dead.
 *
 * The band is a <canvas>, which React cannot describe, so one effect draws it
 * imperatively while React owns the controls, the numbers and the verdict. Every
 * class name is styled by apps/shell.css or by the CSS block at the foot of this
 * file, and the block is part of the component so an app is one file.
 */
import { useEffect, useMemo, useRef, useState } from "react";

import type { AppProps } from "./appProps";

/** A risk posture, as an expected real return and the volatility around it. */
interface RiskLevel {
  name: string;
  /** Expected real return per year, as a fraction. */
  mu: number;
  /** Annual standard deviation of that return. */
  sd: number;
}

/** The five postures the risk slider steps through, least risk first. */
const RISK: RiskLevel[] = [
  { name: "Preservation", mu: 0.03, sd: 0.045 },
  { name: "Conservative", mu: 0.043, sd: 0.07 },
  { name: "Balanced", mu: 0.055, sd: 0.105 },
  { name: "Growth", mu: 0.065, sd: 0.14 },
  { name: "Aggressive", mu: 0.072, sd: 0.175 },
];

/** Where the contribution and risk sliders start, and where Reset puts them. */
const DEFAULT_MONTHLY = 2000;
const DEFAULT_RISK = 2;

/** Standard normal quantile for the 10th and 90th percentiles. */
const Z90 = 1.2815515655446004;

/** Where the account stands today, which is what the app draws from. */
interface Model {
  accountId: string;
  household: string;
  currentAge: number;
  balance: number;
  goal: number;
  currency: string;
  /** The retirement age the sliders open on, never before the client's own. */
  defaultAge: number;
}

/**
 * Narrow the wire data into the numbers this app can project with.
 *
 * Defaults rather than exceptions here: a household whose fields did not arrive
 * still draws a legible projection off a plausible starting point, which is
 * better than a blank pane. Coercing every field through `Number` is what keeps
 * a string "45" from silently becoming "451" when it meets a `+`.
 */
function readModel(data: AppProps["data"]): Model {
  const currentAge = Number(data.current_age ?? 45);
  return {
    accountId: String(data.account_id ?? ""),
    household: String(data.household ?? ""),
    currentAge,
    balance: Number(data.balance ?? 250000),
    goal: Number(data.goal ?? 2000000),
    currency: String(data.currency ?? "$"),
    defaultAge: Math.max(currentAge + 1, Number(data.default_age ?? 65)),
  };
}

/** Short money, because the chart and the verdict have a few pixels each. */
function money(n: number, currency: string): string {
  if (n >= 1e6) return `${currency}${(n / 1e6).toFixed(2)}M`;
  if (n >= 1e3) return `${currency}${Math.round(n / 1e3)}k`;
  return `${currency}${Math.round(n)}`;
}

/** One year of the projection: the 10th percentile, the median, the 90th. */
interface Point {
  t: number;
  lo: number;
  mid: number;
  hi: number;
}

/**
 * Percentile paths for the balance, year by year.
 *
 * Closed-form lognormal rather than sampled Monte Carlo: the median and the
 * 10th/90th band are what the chart shows, and those have exact expressions
 * under the same assumptions a simulation would make. Thousands of sampled paths
 * per slider tick would drop frames for a fuzzier version of the same curve.
 */
function project(years: number, monthly: number, mu: number, sd: number, balance: number): Point[] {
  const out: Point[] = [];
  for (let t = 0; t <= years; t++) {
    // Contributions compound too, so grow each year's inflow for the years it
    // remains invested.
    let contributed = 0;
    for (let y = 0; y < t; y++) contributed += monthly * 12 * Math.pow(1 + mu, t - y - 0.5);
    const grown = balance * Math.pow(1 + mu, t);
    const expected = grown + contributed;
    // Spread applies to the invested balance, widening with the square root of
    // time, as volatility does.
    const spread = sd * Math.sqrt(t);
    out.push({
      t,
      lo: expected * Math.exp(-Z90 * spread - 0.5 * spread * spread),
      mid: expected * Math.exp(-0.5 * spread * spread),
      hi: expected * Math.exp(Z90 * spread - 0.5 * spread * spread),
    });
  }

  return out;
}

/** Abramowitz and Stegun 26.2.17: enough precision for a projection band. */
function normalCdf(z: number): number {
  const t = 1 / (1 + 0.2316419 * Math.abs(z));
  const d = 0.3989422804014327 * Math.exp((-z * z) / 2);
  const p =
    d *
    t *
    (0.31938153 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))));
  return z > 0 ? 1 - p : p;
}

/**
 * Draw the band, the goal line and the median onto the canvas.
 *
 * Imperative on purpose, and the reason the canvas is held by a ref: this is a
 * picture rather than a tree of elements, and describing it as JSX would buy
 * nothing. It reads the palette off the document so the chart follows
 * apps/shell.css instead of carrying its own colours.
 */
function draw(
  canvas: HTMLCanvasElement,
  path: Point[],
  goal: number,
  age: number,
  currency: string,
) {
  const rect = canvas.getBoundingClientRect();
  if (!rect.width) return;

  // The backing store is in device pixels and the drawing is in CSS pixels, so
  // the transform below carries one to the other. Without it the whole chart is
  // soft on any display that is not exactly 1x.
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(rect.width * ratio);
  canvas.height = Math.round(rect.height * ratio);
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  const w = rect.width;
  const h = rect.height;
  ctx.clearRect(0, 0, w, h);

  const style = getComputedStyle(document.documentElement);
  const accent = style.getPropertyValue("--accent").trim() || "#2f6feb";
  const muted = style.getPropertyValue("--muted").trim() || "#6b7280";

  const top = Math.max(goal * 1.15, path[path.length - 1].hi);
  const x = (t: number) => (t / (path.length - 1)) * (w - 6) + 3;
  const y = (v: number) => h - 14 - (v / top) * (h - 22);

  // The band first, so the median and the goal line sit on top of it.
  ctx.beginPath();
  ctx.moveTo(x(path[0].t), y(path[0].hi));
  for (let i = 1; i < path.length; i++) ctx.lineTo(x(path[i].t), y(path[i].hi));
  for (let i = path.length - 1; i >= 0; i--) ctx.lineTo(x(path[i].t), y(path[i].lo));
  ctx.closePath();
  ctx.globalAlpha = 0.18;
  ctx.fillStyle = accent;
  ctx.fill();
  ctx.globalAlpha = 1;

  ctx.beginPath();
  ctx.setLineDash([4, 4]);
  ctx.moveTo(3, y(goal));
  ctx.lineTo(w - 3, y(goal));
  ctx.strokeStyle = muted;
  ctx.lineWidth = 1;
  ctx.stroke();
  ctx.setLineDash([]);

  ctx.beginPath();
  ctx.moveTo(x(path[0].t), y(path[0].mid));
  for (let i = 1; i < path.length; i++) ctx.lineTo(x(path[i].t), y(path[i].mid));
  ctx.strokeStyle = accent;
  ctx.lineWidth = 2;
  ctx.stroke();

  ctx.fillStyle = muted;
  ctx.font = "10px ui-sans-serif, system-ui, sans-serif";
  ctx.fillText("now", 3, h - 3);
  ctx.fillText(`age ${age}`, w - 46, h - 3);
  ctx.fillText(`goal ${money(goal, currency)}`, 6, Math.max(9, y(goal) - 3));
}

/** One labelled trackbar: the name of the setting, its value, and the slider. */
function Control({
  id,
  label,
  ariaLabel,
  value,
  min,
  max,
  step,
  position,
  onChange,
}: {
  id: string;
  label: string;
  ariaLabel: string;
  /** The value as the advisor reads it, already formatted. */
  value: string;
  min: number;
  max: number;
  step: number;
  /** The value as the slider carries it. */
  position: number;
  onChange: (next: number) => void;
}) {
  return (
    <div className="control">
      <div className="head">
        <span className="lbl">{label}</span>
        <b id={`${id}-v`}>{value}</b>
      </div>
      <input
        type="range"
        id={id}
        min={min}
        max={max}
        step={step}
        value={position}
        aria-label={ariaLabel}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </div>
  );
}

/**
 * The app.
 *
 * The projection is recomputed wholesale from the three slider positions on
 * every render rather than patched a field at a time, because the chart, the
 * median and the odds all depend on all three at once and a partial update is
 * how those fall out of step. Height needs no attention: `useApp` in index.tsx
 * turns on the SDK's ResizeObserver, so the frame follows the content.
 */
export function Projection({ data, callTool }: AppProps) {
  const model = useMemo(() => readModel(data), [data]);
  const [age, setAge] = useState(model.defaultAge);
  const [monthly, setMonthly] = useState(DEFAULT_MONTHLY);
  const [riskIndex, setRiskIndex] = useState(DEFAULT_RISK);
  const [msg, setMsg] = useState(
    model.household
      ? `${model.household}: goal ${Math.round(model.goal / 1000)}k`
      : "Model the goal.",
  );
  const chart = useRef<HTMLCanvasElement>(null);

  const risk = RISK[riskIndex];
  const years = Math.max(1, age - model.currentAge);
  const path = useMemo(
    () => project(years, monthly, risk.mu, risk.sd, model.balance),
    [years, monthly, risk.mu, risk.sd, model.balance],
  );
  const end = path[path.length - 1];

  // Where the goal falls in the terminal lognormal, read back as a probability
  // of clearing it. `end.mid` is that distribution's median, so the comparison
  // is against it directly: the drift correction is already inside the number,
  // and applying it twice would overstate every chance the app quotes.
  const spread = risk.sd * Math.sqrt(years) || 1e-9;
  const odds = Math.round(100 * normalCdf(Math.log(end.mid / model.goal) / spread));
  const oddsClass = odds >= 70 ? "good" : odds >= 45 ? "muted" : "bad";

  useEffect(() => {
    const canvas = chart.current;
    if (!canvas) return;

    const paint = () => draw(canvas, path, model.goal, age, model.currency);
    paint();
    // The canvas is sized in percent, so its pixel width changes whenever the
    // host reflows the frame. Observing the element catches that without a
    // resize event, and the drawing writes only to the backing store, so it
    // cannot feed the observer a new size and loop.
    const observer = new ResizeObserver(paint);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [path, age, model.goal, model.currency]);

  async function submit() {
    try {
      // `submit_goal_plan` is `visibility: ["app"]` on the server, so this app
      // is the only caller it has. Nesting is fine here: this is an ordinary
      // tool call, not an elicitation answer restricted to primitives.
      const res = await callTool("submit_goal_plan", {
        account_id: model.accountId,
        plan: {
          retirement_age: age,
          monthly_contribution: monthly,
          risk_level: risk.name,
        },
      });
      const out = (res.structuredContent ?? {}) as { error?: string; retirement_age?: number };
      if (out.error) {
        setMsg(out.error);
        return;
      }

      setMsg(`Plan saved: retire at ${out.retirement_age ?? age}.`);
    } catch (err) {
      // Say what failed. A save that vanishes leaves the advisor believing the
      // household has a plan on file.
      setMsg(`Could not save: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  return (
    <>
      <style>{CSS}</style>

      <p className="msg" id="msg">
        {msg}
      </p>

      <Control
        id="age"
        label="Retirement age"
        ariaLabel="Retirement age"
        value={String(age)}
        // A projection cannot start before the client is older than they are
        // now, so the slider cannot reach back past their current age.
        min={model.currentAge + 1}
        max={75}
        step={1}
        position={age}
        onChange={setAge}
      />
      <Control
        id="cont"
        label="Monthly contribution"
        ariaLabel="Monthly contribution"
        value={`${model.currency}${monthly.toLocaleString()}`}
        min={0}
        max={10000}
        step={250}
        position={monthly}
        onChange={setMonthly}
      />
      <Control
        id="risk"
        label="Risk"
        ariaLabel="Risk level"
        value={risk.name}
        min={0}
        max={RISK.length - 1}
        step={1}
        position={riskIndex}
        onChange={setRiskIndex}
      />

      <div className="card">
        <canvas id="chart" ref={chart} aria-label="Projected balance range"></canvas>
        <div className="legend">
          <span>
            <i className="swatch" style={{ background: "var(--accent)", opacity: 0.18 }}></i>
            10th to 90th percentile
          </span>
          <span>
            <i className="swatch" style={{ background: "var(--accent)" }}></i>Median
          </span>
          <span>
            <i className="swatch" style={{ background: "var(--muted)" }}></i>Goal
          </span>
        </div>
      </div>

      <div className="verdict">
        Median at retirement <b id="median">{money(end.mid, model.currency)}</b>
        <span id="odds" className={oddsClass}>
          {` - ${odds}% chance of reaching ${money(model.goal, model.currency)}`}
        </span>
      </div>

      <div className="row">
        <button type="button" className="primary" id="submit" onClick={submit}>
          Save this plan
        </button>
        {/* Reset, not Cancel. Nothing is waiting on this app: the tool call
            finished before the app was rendered, so there is no pause to back
            out of. Putting all three sliders back where the app opened is the
            useful action, and the plan is three settings, not one. */}
        <button
          type="button"
          id="reset"
          onClick={() => {
            setAge(model.defaultAge);
            setMonthly(DEFAULT_MONTHLY);
            setRiskIndex(DEFAULT_RISK);
          }}
        >
          Reset
        </button>
      </div>
    </>
  );
}

/** Styles for this app alone. apps/shell.css supplies everything shared. */
const CSS = `
  .control { margin-bottom: 9px; }
  .control .head { display: flex; align-items: baseline; gap: 6px; }
  .control .head b { margin-left: auto; font-variant-numeric: tabular-nums; font-size: 12.5px; }
  #chart { display: block; width: 100%; height: 170px; }
  .legend { display: flex; gap: 12px; font-size: 10.5px; color: var(--muted); margin-top: 2px; }
  .swatch { display: inline-block; width: 8px; height: 8px; border-radius: 2px; margin-right: 4px; }
  .verdict { font-size: 12.5px; margin-top: 8px; }
  .verdict b { font-size: 15px; font-variant-numeric: tabular-nums; }
  .good { color: var(--up); }
  .bad { color: var(--down); }
`;
