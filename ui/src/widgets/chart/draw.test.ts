import type { ChartMark } from "../../api/types";
import { AXIS_W, drawChart, priceAtY, readColours, type Colours, type Frame } from "./draw";
import { candle } from "./testdata";

/** A 2D context that records what was drawn, with the style in force at the time. */
function recorder() {
  const calls: { op: string; args: unknown[]; fill: string; stroke: string }[] = [];
  const state = { fillStyle: "", strokeStyle: "" };
  const ctx = new Proxy(state as Record<string, unknown>, {
    get(target, key: string) {
      if (key in target) return target[key];
      return (...args: unknown[]) => {
        calls.push({ op: key, args, fill: String(target.fillStyle), stroke: String(target.strokeStyle) });
      };
    },
    set(target, key: string, value) {
      target[key] = value;
      return true;
    },
  });
  return { ctx: ctx as unknown as CanvasRenderingContext2D, calls };
}

const COLOURS: Colours = { up: "UP", down: "DOWN", grid: "GRID", text: "TEXT", volume: "VOL", buy: "BUY", sell: "SELL", cross: "CROSS", surface: "BG" };
const at = (hhmm: string) => `2026-10-16T${hhmm}:00+05:30`;

function frame(extra: Partial<Frame> = {}): Frame {
  return {
    width: 400 + AXIS_W,
    height: 300,
    candles: [candle(at("09:15"), 1000, 1100, 950, 1080, 50), candle(at("09:16"), 1080, 1090, 990, 1000, 100)],
    view: { barW: 10, offset: 0 },
    marks: new Map(),
    hover: { i: null, y: null },
    daily: false,
    colours: COLOURS,
    ...extra,
  };
}

describe("drawing a chart", () => {
  it("nothing to draw: just the background", () => {
    const { ctx, calls } = recorder();
    drawChart(ctx, frame({ candles: [] }));
    expect(calls.map((c) => [c.op, c.fill])).toEqual([["fillRect", "BG"]]);
  });

  it("up candles hollow, down candles filled, volume under them, the last price on the axis", () => {
    const { ctx, calls } = recorder();
    drawChart(ctx, frame());
    const rects = calls.filter((c) => c.op === "strokeRect" || c.op === "fillRect");
    expect(rects.filter((c) => c.op === "strokeRect").map((c) => c.stroke)).toEqual(["UP"]); // the 09:15 candle rose
    const filled = rects.filter((c) => c.op === "fillRect").map((c) => c.fill);
    expect(filled.filter((f) => f === "DOWN").length).toBe(2); // the 09:16 body and the last-price box
    expect(filled.filter((f) => f === "VOL").length).toBe(2);
    const volumes = rects.filter((c) => c.fill === "VOL").map((c) => c.args[3] as number);
    expect(volumes[1]).toBeCloseTo(volumes[0]! * 2); // 100 against 50
    const texts = calls.filter((c) => c.op === "fillText").map((c) => c.args[0]);
    expect(texts).toContain("10.00"); // the last close, on the axis
    expect(texts).toContain("10-16"); // the first candle starts a day: its date
  });

  it("fills: ▲ buys under the candle, ▼ sells over it; off-screen ones aren't drawn", () => {
    const mark = (side: "BUY" | "SELL"): ChartMark => ({ t: at("09:16"), side, qty: 75, price_paise: 1000, client_order_id: side, strategy: "manual" });
    const { ctx, calls } = recorder();
    drawChart(ctx, frame({ marks: new Map([[1, [mark("BUY"), mark("SELL")]], [5, [mark("BUY")]]]) }));
    const fills = calls.filter((c) => c.op === "fill").map((c) => c.fill);
    expect(fills).toEqual(["BUY", "SELL"]);
    const moves = calls.filter((c) => c.op === "moveTo" && (c.fill === "BUY" || c.fill === "SELL"));
    const buyTip = moves.find((c) => c.fill === "BUY")!.args[1] as number;
    const sellTip = moves.find((c) => c.fill === "SELL")!.args[1] as number;
    expect(buyTip).toBeGreaterThan(sellTip); // the buy sits lower on screen
  });

  it("the crosshair: dashed lines and the price under the pointer", () => {
    const { ctx, calls } = recorder();
    const f = frame({ hover: { i: 1, y: 100 } });
    drawChart(ctx, f);
    expect(calls.filter((c) => c.op === "setLineDash").map((c) => c.args[0])).toEqual([[3, 3], []]);
    const label = calls.filter((c) => c.op === "fillText").map((c) => c.args[0]);
    const at100 = priceAtY(100, f)!;
    expect(label).toContain((Math.round(at100) / 100).toFixed(2));
    const snapped = recorder();
    drawChart(snapped.ctx, { ...f, tick: 5 });
    const labels = snapped.calls.filter((c) => c.op === "fillText").map((c) => c.args[0]);
    expect(labels).toContain((Math.round(Math.round(at100) / 5) * 5 / 100).toFixed(2)); // the price a click would pick
  });

  it("the price under the pointer, and none outside the price pane", () => {
    const f = frame();
    const top = priceAtY(4, f)!;
    const lower = priceAtY(200, f)!;
    expect(top).toBeGreaterThan(lower);
    expect(top).toBeGreaterThan(1100); // the highest high, plus room
    expect(priceAtY(-1, f)).toBeNull();
    expect(priceAtY(290, f)).toBeNull(); // the volume pane
    expect(priceAtY(50, frame({ candles: [] }))).toBeNull();
  });

  it("the theme gives the colours; defaults stand in", () => {
    const el = document.createElement("div");
    el.style.setProperty("--candle-up", "#123456");
    document.body.append(el);
    expect(readColours(el).up).toBe("#123456");
    expect(readColours(null).down).toBe("#d95926");
    el.remove();
  });
});
