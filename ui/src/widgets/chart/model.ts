// Chart logic, kept out of the widget so it can be tested without a canvas: merging candles
// (history, older pages, the forming one), the view (how many bars, how far from the newest),
// scales, axis labels and what is under the pointer. Every candle comes from the server
// (docs/design/charts.md); nothing here knows market hours or intervals.

import type { Candle, ChartMark } from "../../api/types";

// --- the candle list ---------------------------------------------------------------------

/** The forming candle from the stream: replaces the same bucket, appends a newer one, and an
 *  older one (a late message) changes nothing. */
export function mergeForming(list: Candle[], c: Candle | null): Candle[] {
  if (!c) return list;
  const last = list[list.length - 1];
  if (!last || c.t > last.t) return [...list, c];
  if (c.t === last.t) return [...list.slice(0, -1), c];
  return list;
}

/** An older page joined in front: only candles before the first one we have. */
export function mergeOlder(older: Candle[], list: Candle[]): Candle[] {
  const first = list[0]?.t;
  const keep = first === undefined ? older : older.filter((c) => c.t < first);
  return [...keep, ...list];
}

// --- the view ----------------------------------------------------------------------------

export interface View {
  barW: number; // pixels per candle (zoom)
  offset: number; // whole candles between the newest one and the right edge (pan; 0 = newest shown)
}

export const MIN_BAR = 3;
export const MAX_BAR = 40;
export const DEFAULT_VIEW: View = { barW: 8, offset: 0 };

/** The candles on screen: [from, to) indexes into the list. */
export function visible(view: View, n: number, plotW: number): { from: number; to: number } {
  const count = Math.max(1, Math.floor(plotW / view.barW));
  const to = Math.max(0, n - view.offset);
  return { from: Math.max(0, to - count), to };
}

/** Pan by whole candles (positive: back in time), never past either end. */
export function pan(view: View, bars: number, n: number, plotW: number): View {
  const count = Math.max(1, Math.floor(plotW / view.barW));
  const most = Math.max(0, n - Math.min(count, n));
  return { ...view, offset: Math.min(most, Math.max(0, Math.round(view.offset + bars))) };
}

/** Zoom by a factor around the newest visible candle (the right edge stays put). */
export function zoom(view: View, factor: number): View {
  return { ...view, barW: Math.min(MAX_BAR, Math.max(MIN_BAR, view.barW * factor)) };
}

/** Ask for older candles when fewer than `margin` are left off the left edge. */
export function needsOlder(view: View, n: number, plotW: number, margin = 20): boolean {
  return visible(view, n, plotW).from < margin;
}

// --- scales ------------------------------------------------------------------------------

export interface Scale {
  min: number;
  max: number;
  y: (paise: number) => number;
  price: (y: number) => number;
}

/** Prices of the visible candles to pixels, a little room above and below; a flat stretch
 *  gets a band so it isn't a line on the edge. */
export function priceScale(candles: Candle[], top: number, height: number): Scale {
  let min = Math.min(...candles.map((c) => c.l));
  let max = Math.max(...candles.map((c) => c.h));
  if (!Number.isFinite(min)) {
    min = 0;
    max = 1;
  }
  const pad = max > min ? (max - min) * 0.06 : Math.max(1, Math.abs(max) * 0.001);
  min -= pad;
  max += pad;
  const y = (p: number) => top + ((max - p) * height) / (max - min);
  const price = (py: number) => max - ((py - top) * (max - min)) / height;
  return { min, max, y, price };
}

/** About `count` round price levels between min and max (1, 2, 2.5 or 5 × a power of ten). */
export function priceTicks(min: number, max: number, count = 5): number[] {
  if (!(max > min)) return [min];
  const raw = (max - min) / count;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * power).find((s) => s >= raw)!;
  const out: number[] = [];
  for (let v = Math.ceil(min / step) * step; v <= max; v += step) out.push(Math.round(v));
  return out;
}

/** Snap a price to the instrument's tick (a chart click picks a tradable price). */
export function snapTick(paise: number, tick: number): number {
  return Math.max(tick, Math.round(paise / tick) * tick);
}

// --- time labels ---------------------------------------------------------------------------

const IST_OFFSET_MS = 330 * 60_000;

/** The India date and time of a candle (its `t` carries +05:30; read it so in any browser). */
export function indiaParts(t: string): { date: string; time: string } {
  const ms = Date.parse(t) + IST_OFFSET_MS;
  const iso = new Date(ms).toISOString();
  return { date: iso.slice(0, 10), time: iso.slice(11, 16) };
}

/** Labels along the time axis: about one every `every` pixels; where a new day starts (on an
 *  intraday chart) its date instead, and a regular label crowding a date is dropped. */
export function timeLabels(candles: Candle[], from: number, to: number, barW: number, daily: boolean, every = 90): { i: number; text: string }[] {
  const step = Math.max(1, Math.round(every / barW));
  const out: { i: number; text: string; day: boolean }[] = [];
  let lastDate = from > 0 && candles[from - 1] ? indiaParts(candles[from - 1]!.t).date : "";
  for (let i = from; i < to; i++) {
    const p = indiaParts(candles[i]!.t);
    const newDay = p.date !== lastDate;
    lastDate = p.date;
    if (newDay && !daily) out.push({ i, text: p.date.slice(5), day: true });
    else if ((i - from) % step === 0) out.push({ i, text: daily ? p.date.slice(5) : p.time, day: false });
  }
  return out.filter((l) => l.day || !out.some((o) => o.day && Math.abs(o.i - l.i) < step)).map(({ i, text }) => ({ i, text }));
}

// --- marks -----------------------------------------------------------------------------------

/** Each fill placed on the candle it happened in (the last candle starting at or before it). */
export function marksByCandle(candles: Candle[], marks: ChartMark[]): Map<number, ChartMark[]> {
  const out = new Map<number, ChartMark[]>();
  const starts = candles.map((c) => Date.parse(c.t));
  for (const m of marks) {
    const at = Date.parse(m.t);
    let lo = 0;
    let hi = starts.length - 1;
    if (hi < 0 || at < starts[0]!) continue;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (starts[mid]! <= at) lo = mid;
      else hi = mid - 1;
    }
    const list = out.get(lo) ?? [];
    list.push(m);
    out.set(lo, list);
  }
  return out;
}

/** The candle index under a pointer at x (inside the plot), or null. */
export function indexAt(x: number, view: View, n: number, plotW: number): number | null {
  const { from, to } = visible(view, n, plotW);
  if (to <= from) return null;
  const i = to - Math.ceil((plotW - x) / view.barW); // the newest candle ends at the right edge
  return i >= from && i < to ? i : null;
}

/** The x centre of candle i on screen. */
export function xOf(i: number, view: View, n: number, plotW: number): number {
  const { to } = visible(view, n, plotW);
  return plotW - (to - i - 0.5) * view.barW;
}
