// Drawing a chart on a canvas: candles (up hollow, down filled: shape as well as colour, so
// colour-blind readers and greyscale prints read them), the volume pane, price and time axes,
// trade marks (▲ buys under the candle, ▼ sells over it) and the crosshair. The numbers are
// worked out in model.ts; this only paints them.

import type { Candle, ChartMark } from "../../api/types";
import { price as fmtPrice } from "../../lib/format";
import { priceScale, priceTicks, timeLabels, visible, type View, xOf } from "./model";

export interface Colours {
  up: string;
  down: string;
  grid: string;
  text: string;
  volume: string;
  buy: string;
  sell: string;
  cross: string;
  surface: string;
}

/** The colours from the theme (CSS custom properties on the chart), with safe defaults. */
export function readColours(el: Element | null): Colours {
  const css = el ? getComputedStyle(el) : null;
  const get = (name: string, fallback: string) => css?.getPropertyValue(name).trim() || fallback;
  return {
    up: get("--candle-up", "#3987e5"),
    down: get("--candle-down", "#d95926"),
    grid: get("--chart-grid", "#2b333e"),
    text: get("--text-2", "#8d99a8"),
    volume: get("--vap-bar", "#34506f"),
    buy: get("--buy-text", "#6fa8ff"),
    sell: get("--sell-text", "#ff7b83"),
    cross: get("--text-3", "#aeb8c5"),
    surface: get("--well", "#10141a"),
  };
}

export const AXIS_W = 72; // the price axis on the right
export const TIME_H = 18; // the time axis at the bottom
export const VOLUME_SHARE = 0.18; // of the plot height

export interface Frame {
  width: number;
  height: number;
  candles: Candle[];
  view: View;
  marks: Map<number, ChartMark[]>;
  hover: { i: number | null; y: number | null };
  daily: boolean;
  colours: Colours;
}

/** Paint one frame. `ctx` is any 2D context (a recording fake in tests). */
export function drawChart(ctx: CanvasRenderingContext2D, f: Frame): void {
  const plotW = Math.max(10, f.width - AXIS_W);
  const plotH = Math.max(10, f.height - TIME_H);
  const volH = Math.round(plotH * VOLUME_SHARE);
  const priceH = plotH - volH - 4;
  const { from, to } = visible(f.view, f.candles.length, plotW);
  const shown = f.candles.slice(from, to);
  ctx.fillStyle = f.colours.surface;
  ctx.fillRect(0, 0, f.width, f.height);
  if (!shown.length) return;
  const scale = priceScale(shown, 4, priceH - 8);
  const maxVol = Math.max(1, ...shown.map((c) => c.v));
  const n = f.candles.length;
  const body = Math.max(1, Math.floor(f.view.barW * 0.7));
  ctx.font = "11px system-ui, sans-serif";

  // grid and price axis
  ctx.strokeStyle = f.colours.grid;
  ctx.fillStyle = f.colours.text;
  ctx.lineWidth = 1;
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  for (const level of priceTicks(scale.min, scale.max)) {
    const y = Math.round(scale.y(level)) + 0.5;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(plotW, y);
    ctx.stroke();
    ctx.fillText(fmtPrice(level), plotW + 6, y);
  }

  // time axis
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  for (const label of timeLabels(f.candles, from, to, f.view.barW, f.daily)) {
    ctx.fillText(label.text, xOf(label.i, f.view, n, plotW), plotH + 3);
  }

  // volume and candles
  for (let i = from; i < to; i++) {
    const c = f.candles[i]!;
    const x = xOf(i, f.view, n, plotW);
    const up = c.c >= c.o;
    const colour = up ? f.colours.up : f.colours.down;
    const vh = (c.v / maxVol) * volH;
    ctx.fillStyle = f.colours.volume;
    ctx.fillRect(Math.round(x - body / 2), plotH - vh, body, vh);
    ctx.strokeStyle = colour;
    ctx.fillStyle = colour;
    ctx.beginPath(); // the wick
    ctx.moveTo(Math.round(x) + 0.5, scale.y(c.h));
    ctx.lineTo(Math.round(x) + 0.5, scale.y(c.l));
    ctx.stroke();
    const top = scale.y(Math.max(c.o, c.c));
    const height = Math.max(1, scale.y(Math.min(c.o, c.c)) - top);
    if (up) ctx.strokeRect(Math.round(x - body / 2) + 0.5, Math.round(top) + 0.5, body - 1, Math.max(0, height - 1));
    else ctx.fillRect(Math.round(x - body / 2), Math.round(top), body, height);
  }

  // trade marks
  for (const [i, marks] of f.marks) {
    if (i < from || i >= to) continue;
    const c = f.candles[i]!;
    const x = xOf(i, f.view, n, plotW);
    marks.forEach((m, k) => {
      const buy = m.side === "BUY";
      const y = buy ? scale.y(c.l) + 8 + k * 9 : scale.y(c.h) - 8 - k * 9;
      ctx.fillStyle = buy ? f.colours.buy : f.colours.sell;
      ctx.strokeStyle = f.colours.surface;
      ctx.beginPath();
      if (buy) {
        ctx.moveTo(x, y - 4);
        ctx.lineTo(x + 4, y + 3);
        ctx.lineTo(x - 4, y + 3);
      } else {
        ctx.moveTo(x, y + 4);
        ctx.lineTo(x + 4, y - 3);
        ctx.lineTo(x - 4, y - 3);
      }
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
    });
  }

  // the last price on the axis
  const last = f.candles[n - 1]!;
  ctx.fillStyle = last.c >= last.o ? f.colours.up : f.colours.down;
  const ly = scale.y(last.c);
  if (ly >= 0 && ly <= priceH) {
    ctx.fillRect(plotW + 1, ly - 8, AXIS_W - 2, 16);
    ctx.fillStyle = f.colours.surface;
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    ctx.fillText(fmtPrice(last.c), plotW + 6, ly);
  }

  // the crosshair
  if (f.hover.i !== null || f.hover.y !== null) {
    ctx.strokeStyle = f.colours.cross;
    ctx.setLineDash([3, 3]);
    if (f.hover.i !== null) {
      const x = Math.round(xOf(f.hover.i, f.view, n, plotW)) + 0.5;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, plotH);
      ctx.stroke();
    }
    if (f.hover.y !== null && f.hover.y <= priceH) {
      const y = Math.round(f.hover.y) + 0.5;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(plotW, y);
      ctx.stroke();
      ctx.fillStyle = f.colours.cross;
      ctx.fillRect(plotW + 1, y - 8, AXIS_W - 2, 16);
      ctx.fillStyle = f.colours.surface;
      ctx.fillText(fmtPrice(Math.round(scale.price(f.hover.y))), plotW + 6, y);
    }
    ctx.setLineDash([]);
  }
}

/** The price under a pointer at y, using the same scale the frame was drawn with. */
export function priceAtY(y: number, f: Pick<Frame, "width" | "height" | "candles" | "view">): number | null {
  const plotW = Math.max(10, f.width - AXIS_W);
  const plotH = Math.max(10, f.height - TIME_H);
  const priceH = plotH - Math.round(plotH * VOLUME_SHARE) - 4;
  if (y < 0 || y > priceH) return null;
  const { from, to } = visible(f.view, f.candles.length, plotW);
  const shown = f.candles.slice(from, to);
  if (!shown.length) return null;
  return priceScale(shown, 4, priceH - 8).price(y);
}
