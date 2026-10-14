// Strategy builder logic: the payoff chart's geometry and the numbers' words. The server does
// every calculation (options/strategy.py); this only lays it out.

import type { StrategyAnalysis, StrategyLegIn } from "../../api/types";

export interface Box {
  width: number;
  height: number;
  pad: number;
}

export interface Chart {
  expiryPath: string;
  todayPath: string;
  zeroY: number;
  forwardX: number;
  breakevenXs: number[];
  yMin: number;
  yMax: number;
  xMin: number;
  xMax: number;
  profitArea: string; // the area above zero under the expiry line (shaded green)
}

/** Scale the server's curves into an SVG box. P&L axis includes 0 and both curves. */
export function chart(a: StrategyAnalysis, box: Box): Chart {
  const xs = a.prices;
  const ys = [...a.payoff_expiry, ...a.payoff_today, 0];
  const xMin = xs[0]!;
  const xMax = xs[xs.length - 1]!;
  let yMin = Math.min(...ys);
  let yMax = Math.max(...ys);
  const room = (yMax - yMin || 1) * 0.08;
  yMin -= room;
  yMax += room;
  const sx = (x: number) => box.pad + ((x - xMin) / (xMax - xMin || 1)) * (box.width - 2 * box.pad);
  const sy = (y: number) => box.height - box.pad - ((y - yMin) / (yMax - yMin)) * (box.height - 2 * box.pad);
  const path = (series: number[]) => series.map((y, i) => `${i ? "L" : "M"}${sx(xs[i]!).toFixed(1)},${sy(y).toFixed(1)}`).join("");
  const zeroY = sy(0);
  const clipped = a.payoff_expiry.map((y) => Math.max(y, 0));
  const profitArea = `${path(clipped)}L${sx(xMax).toFixed(1)},${zeroY.toFixed(1)}L${sx(xMin).toFixed(1)},${zeroY.toFixed(1)}Z`;
  return {
    expiryPath: path(a.payoff_expiry),
    todayPath: path(a.payoff_today),
    zeroY,
    forwardX: sx(a.forward_paise / 100),
    breakevenXs: a.breakevens.filter((b) => b >= xMin && b <= xMax).map(sx),
    yMin,
    yMax,
    xMin,
    xMax,
    profitArea,
  };
}

const INR = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

/** ₹ with a sign: +₹7,196 / −₹7,804; "Unlimited" stays a word. */
export function money(v: number | "unlimited", signed = true): string {
  if (v === "unlimited") return "Unlimited";
  const text = `₹${INR.format(Math.abs(Math.round(v)))}`;
  if (!signed || v === 0) return text;
  return v > 0 ? `+${text}` : `−${text}`;
}

/** "Credit ₹7,196" / "Debit ₹6,251": what the trade does to cash at entry. */
export function premiumText(v: number): string {
  if (Math.abs(v) < 0.5) return "No net premium";
  return `${v > 0 ? "Credit" : "Debit"} ${money(Math.abs(v), false)}`;
}

/** Legs edited in the widget: flip a side, change lots (1–100), remove one. */
export function flip(legs: StrategyLegIn[], i: number): StrategyLegIn[] {
  return legs.map((l, j) => (j === i ? { ...l, side: l.side === "BUY" ? "SELL" : "BUY", price_paise: undefined } : l));
}

export function setLots(legs: StrategyLegIn[], i: number, lots: number): StrategyLegIn[] {
  const n = Math.max(1, Math.min(100, Math.round(lots) || 1));
  return legs.map((l, j) => (j === i ? { ...l, lots: n } : l));
}

export function removeLeg(legs: StrategyLegIn[], i: number): StrategyLegIn[] {
  return legs.filter((_, j) => j !== i);
}

/** Add a leg from the chain; the same contract and side adds lots instead of a new row. */
export function addLeg(legs: StrategyLegIn[], leg: StrategyLegIn): StrategyLegIn[] {
  const same = legs.findIndex((l) => l.instrument_id === leg.instrument_id && l.side === leg.side);
  if (same >= 0) return setLots(legs, same, legs[same]!.lots + leg.lots);
  return legs.length >= 8 ? legs : [...legs, leg];
}
