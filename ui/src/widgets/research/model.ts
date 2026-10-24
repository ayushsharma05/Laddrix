// Research screens logic (K11.4), kept out of the widgets so it can be tested on its own:
// sizes and states in words, the backtest request built from the server's form, and the
// numbers behind the SVG charts. Every figure comes from the server (docs/design/research.md).

import type { BacktestDay, BacktestForm, BacktestRun, BacktestSummary, Side } from "../../api/types";
import { shortName } from "../../lib/format";
import { buildAlgoBody, type FormValues } from "../algos/model";
import { parsePrice } from "../ticket/model";

/** Bytes as people read them: 1536 -> "1.5 KB". */
export function bytesText(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let u = 0;
  while (v >= 1024 && u < units.length - 1) {
    v /= 1024;
    u += 1;
  }
  return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${units[u]}`;
}

// --- runs ----------------------------------------------------------------------------------

export const isFinished = (r: BacktestRun) => r.state === "done" || r.state === "failed" || r.state === "cancelled";

/** Keep asking the server while any run is still going. */
export const anyGoing = (runs: BacktestRun[]) => runs.some((r) => !isFinished(r));

/** A run's name: the one given, else what it ran ("SLICER BUY 150 NIFTY26OCTFUT"). */
export function runName(r: BacktestRun): string {
  const s = r.spec;
  return s.name || `${s.algo} ${s.side} ${s.qty} ${shortName(s.instrument_id)}`;
}

/** The run's state in words, with a tone for its colour. */
export function runState(r: BacktestRun): { text: string; tone: "good" | "bad" | "dim" | "pending" } {
  if (r.state === "queued") return { text: "Waiting for the run before it", tone: "pending" };
  if (r.state === "running") return { text: `Running: ${r.progress} of ${r.days} days`, tone: "pending" };
  if (r.state === "failed") return { text: `Failed: ${r.error}`, tone: "bad" };
  if (r.state === "cancelled") return { text: "Cancelled", tone: "dim" };
  const skipped = r.summary?.days_skipped.length ?? 0;
  return { text: skipped ? `Done (${skipped} day${skipped > 1 ? "s" : ""} skipped)` : "Done", tone: "good" };
}

export const isSkipped = (d: BacktestDay): d is { day: string; skipped: string } => "skipped" in d;

export type RanDay = Exclude<BacktestDay, { day: string; skipped: string }>;

export function dayUrl(runId: string, day: string, format: "csv" | "pdf"): string {
  return `/api/research/backtests/${encodeURIComponent(runId)}/days/${day}?format=${format}`;
}

/** Win rate as a percent ("66.7%"), or "–" when no day ran. */
export function winRate(s: BacktestSummary): string {
  return s.win_rate === null ? "–" : `${(s.win_rate * 100).toFixed(1)}%`;
}

// --- the request ---------------------------------------------------------------------------

export interface BacktestInput {
  form: BacktestForm;
  algo: string;
  values: FormValues;
  instrumentId: string | null;
  side: Side;
  lots: number;
  lotSize: number;
  product: string;
  days: string[];
  start: string;
  capital: string; // rupees, as typed
  name: string;
  candles: boolean;
}

export interface BacktestBody {
  days: string[];
  algo: string;
  instrument_id: string;
  side: Side;
  qty: number;
  params: Record<string, unknown>;
  product: string;
  start: string;
  capital_paise: number;
  name: string;
  candles: boolean;
}

/** The POST body, or every problem with the form (the server checks everything again). */
export function buildBacktestBody(t: BacktestInput): { body: BacktestBody | null; problems: string[] } {
  const spec = t.form.algos.find((a) => a.code === t.algo);
  if (!spec) return { body: null, problems: ["Pick an algo"] };
  const algo = buildAlgoBody({ spec, values: t.values, instrumentId: t.instrumentId, side: t.side, lots: t.lots, lotSize: t.lotSize, product: t.product });
  const problems = [...algo.problems];
  if (!t.days.length) problems.push("Pick at least one day");
  if (t.days.length > t.form.max_days) problems.push(`At most ${t.form.max_days} days in one run`);
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(t.start)) problems.push("Start must be a time like 09:20");
  const capital = parsePrice(t.capital);
  if (capital === null) problems.push("Capital must be an amount above ₹0");
  if (problems.length || !algo.body || capital === null) return { body: null, problems };
  const days = [...t.days].sort(); // a run goes through its days in order
  return {
    body: {
      days,
      algo: spec.code,
      instrument_id: algo.body.instrument_id,
      side: t.side,
      qty: algo.body.qty,
      params: algo.body.params,
      product: t.product || t.form.defaults.product,
      start: t.start,
      capital_paise: capital,
      name: t.name.trim(),
      candles: t.candles,
    },
    problems,
  };
}

/** The days the form can offer: recorded days, or the instrument's candle days. */
export function offeredDays(form: BacktestForm | null, candles: boolean): string[] {
  if (!form) return [];
  return candles ? (form.candle_days ?? []) : form.days;
}

// --- charts --------------------------------------------------------------------------------

export interface Series {
  key: string;
  label: string;
  points: (number | null)[]; // one per x; null: no value there
  slot?: number; // its colour slot (Compare); the first slot otherwise
}

export interface Plot {
  x: (i: number) => number;
  y: (v: number) => number;
  min: number;
  max: number;
  zero: number | null; // y of the zero line, when 0 is inside the range
  paths: string[]; // one SVG path per series ("" when it has no points)
}

/** Scales and paths for line series over `n` x positions in a w×h box (pad on every side).
 *  A flat series gets a band around its value; 0 is always shown (profit vs loss). */
export function plot(series: Series[], n: number, w: number, h: number, pad = 4): Plot {
  const values = series.flatMap((s) => s.points.filter((v): v is number => v !== null));
  let min = Math.min(0, ...values);
  let max = Math.max(0, ...values);
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const x = (i: number) => (n <= 1 ? w / 2 : pad + (i * (w - 2 * pad)) / (n - 1));
  const y = (v: number) => pad + ((max - v) * (h - 2 * pad)) / (max - min);
  const paths = series.map((s) => {
    let d = "";
    let pen = false;
    s.points.forEach((v, i) => {
      if (v === null) {
        pen = false; // a gap: lift the pen
        return;
      }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      pen = true;
    });
    return d;
  });
  return { x, y, min, max, zero: min <= 0 && max >= 0 ? y(0) : null, paths };
}

/** The x position nearest a pointer at `px` (for the hover crosshair). */
export function nearest(px: number, n: number, w: number, pad = 4): number {
  if (n <= 1) return 0;
  const i = Math.round(((px - pad) * (n - 1)) / (w - 2 * pad));
  return Math.min(n - 1, Math.max(0, i));
}

/** Runs side by side: every day any of them ran (in order), each run's cumulative net on
 *  its own days and a gap on the others. */
export function compareSeries(picks: { run: BacktestRun; slot: number }[]): { days: string[]; series: Series[] } {
  const days = [...new Set(picks.flatMap(({ run }) => (run.summary?.equity_curve ?? []).map(([d]) => d)))].sort();
  const series = picks.map(({ run, slot }) => {
    const byDay = new Map(run.summary?.equity_curve ?? []);
    return { key: run.run_id, label: runName(run), slot, points: days.map((d) => byDay.get(d) ?? null) };
  });
  return { days, series };
}

/** A day's intraday curve (P&L before charges, sampled each minute) as one series. */
export function intraday(d: RanDay): { times: string[]; series: Series[] } {
  return {
    times: d.equity_curve.map(([t]) => t),
    series: [{ key: d.day, label: `${d.day} P&L`, points: d.equity_curve.map(([, v]) => v) }],
  };
}

/** The run's curve over its days (cumulative net). */
export function runCurve(r: BacktestRun): { days: string[]; series: Series[] } {
  const curve = r.summary?.equity_curve ?? [];
  return { days: curve.map(([d]) => d), series: [{ key: r.run_id, label: "Cumulative net", points: curve.map(([, v]) => v) }] };
}

/** At most this many runs are compared: one colour slot each. */
export const COMPARE_MAX = 4;

/** Compare's picks as colour slots: a run keeps its slot (and colour) while others come and
 *  go; a new pick takes the lowest free slot; when all are taken nothing more is added. */
export function togglePick(slots: (string | null)[], runId: string): (string | null)[] {
  const next = Array.from({ length: COMPARE_MAX }, (_, i) => slots[i] ?? null);
  const at = next.indexOf(runId);
  if (at >= 0) {
    next[at] = null;
    return next;
  }
  const free = next.indexOf(null);
  if (free >= 0) next[free] = runId;
  return next;
}
