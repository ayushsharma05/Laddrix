import type { ChartMark } from "../../api/types";
import { candle } from "./testdata";
import { DEFAULT_VIEW, MAX_BAR, MIN_BAR, indexAt, indiaParts, marksByCandle, mergeForming, mergeOlder, needsOlder, pan, priceScale, priceTicks, snapTick, timeLabels, visible, xOf, zoom } from "./model";


const at = (hhmm: string, day = "2026-10-16") => `${day}T${hhmm}:00+05:30`;

describe("chart model", () => {
  it("the forming candle replaces its bucket, adds a newer one, and a late one changes nothing", () => {
    const list = [candle(at("09:15"), 100), candle(at("09:16"), 101)];
    const same = mergeForming(list, candle(at("09:16"), 101, 105, 100, 104, 30));
    expect(same.map((c) => [c.t.slice(11, 16), c.c])).toEqual([["09:15", 100], ["09:16", 104]]);
    const next = mergeForming(same, candle(at("09:17"), 104));
    expect(next).toHaveLength(3);
    expect(mergeForming(next, candle(at("09:15"), 1))).toBe(next); // late
    expect(mergeForming(next, null)).toBe(next);
    expect(mergeForming([], candle(at("09:15"), 1))).toHaveLength(1);
  });

  it("an older page joins in front, never doubling a candle", () => {
    const list = [candle(at("09:16"), 2), candle(at("09:17"), 3)];
    const older = [candle(at("09:14"), 0), candle(at("09:15"), 1), candle(at("09:16"), 99)];
    expect(mergeOlder(older, list).map((c) => c.o)).toEqual([0, 1, 2, 3]);
    expect(mergeOlder(older, [])).toEqual(older);
  });

  it("the view: what is on screen, panning stops at both ends, zoom stays in bounds", () => {
    expect(visible({ barW: 10, offset: 0 }, 100, 300)).toEqual({ from: 70, to: 100 });
    expect(visible({ barW: 10, offset: 20 }, 100, 300)).toEqual({ from: 50, to: 80 });
    expect(visible({ barW: 10, offset: 0 }, 12, 300)).toEqual({ from: 0, to: 12 });
    expect(pan({ barW: 10, offset: 0 }, 25, 100, 300).offset).toBe(25);
    expect(pan({ barW: 10, offset: 60 }, 50, 100, 300).offset).toBe(70); // the oldest candle reaches the left edge
    expect(pan({ barW: 10, offset: 5 }, -20, 100, 300).offset).toBe(0); // the newest at the right edge
    expect(pan({ barW: 10, offset: 0 }, 10, 12, 300).offset).toBe(0); // everything fits: nowhere to go
    expect(pan({ barW: 10, offset: 0 }, 2.6, 100, 300).offset).toBe(3); // whole candles
    expect(zoom(DEFAULT_VIEW, 100).barW).toBe(MAX_BAR);
    expect(zoom(DEFAULT_VIEW, 0.01).barW).toBe(MIN_BAR);
    expect(zoom({ barW: 10, offset: 4 }, 1.5)).toEqual({ barW: 15, offset: 4 });
    expect(needsOlder({ barW: 10, offset: 0 }, 100, 300)).toBe(false); // 70 left off screen
    expect(needsOlder({ barW: 10, offset: 55 }, 100, 300)).toBe(true); // 15 left
    expect(needsOlder({ barW: 10, offset: 50 }, 100, 300)).toBe(false); // 20 left: not yet
  });

  it("x and the candle under the pointer agree, the newest ending at the right edge", () => {
    const view = { barW: 10, offset: 0 };
    expect(xOf(99, view, 100, 300)).toBe(295);
    expect(xOf(70, view, 100, 300)).toBe(5);
    expect(indexAt(295, view, 100, 300)).toBe(99);
    expect(indexAt(299.9, view, 100, 300)).toBe(99);
    expect(indexAt(289, view, 100, 300)).toBe(98);
    expect(indexAt(0, view, 100, 300)).toBe(70);
    expect(indexAt(5, { barW: 10, offset: 0 }, 12, 300)).toBeNull(); // left of the first candle
    expect(indexAt(5, view, 0, 300)).toBeNull();
    for (const i of [70, 85, 99]) expect(indexAt(xOf(i, view, 100, 300), view, 100, 300)).toBe(i);
  });

  it("prices to pixels and back, a band for a flat stretch, round levels, snapped picks", () => {
    const s = priceScale([candle(at("09:15"), 1000, 1100, 900, 1000)], 0, 100);
    expect(s.min).toBe(888);
    expect(s.max).toBe(1112);
    expect(s.y(1112)).toBe(0);
    expect(s.y(888)).toBe(100);
    expect(s.price(s.y(1000))).toBeCloseTo(1000);
    const flat = priceScale([candle(at("09:15"), 250000)], 0, 100);
    expect(flat.max).toBeGreaterThan(250000);
    expect(flat.min).toBeLessThan(250000);
    expect(priceScale([], 0, 100).max).toBeGreaterThan(priceScale([], 0, 100).min);
    expect(priceTicks(2500000, 2510000, 5)).toEqual([2500000, 2502000, 2504000, 2506000, 2508000, 2510000]);
    expect(priceTicks(5, 5)).toEqual([5]);
    expect(priceTicks(0, 25, 5)).toEqual([0, 5, 10, 15, 20, 25]);
    expect(snapTick(2499987, 10)).toBe(2499990);
    expect(snapTick(2499984, 10)).toBe(2499980);
    expect(snapTick(2, 5)).toBe(5); // never below one tick
  });

  it("time labels read India time in any browser; a new day shows its date", () => {
    expect(indiaParts("2026-10-16T09:15:00+05:30")).toEqual({ date: "2026-10-16", time: "09:15" });
    expect(indiaParts("2026-10-16T18:45:00Z")).toEqual({ date: "2026-10-17", time: "00:15" });
    const day1 = ["15:27", "15:28", "15:29"].map((t) => candle(at(t), 1));
    const day2 = ["09:15", "09:16", "09:17", "09:18"].map((t) => candle(at(t, "2026-10-17"), 1));
    const all = [...day1, ...day2];
    expect(timeLabels(all, 0, all.length, 30, false, 60)).toEqual([
      { i: 0, text: "10-16" },
      { i: 3, text: "10-17" }, // 15:29 and 09:16 would crowd it: dropped
      { i: 6, text: "09:18" },
    ]);
    expect(timeLabels(all, 1, all.length, 30, false, 60)[0]).toEqual({ i: 1, text: "15:28" }); // the day began off screen
    const days = ["2026-10-14", "2026-10-15", "2026-10-16"].map((d) => candle(`${d}T00:00:00+05:30`, 1));
    expect(timeLabels(days, 0, 3, 50, true, 100)).toEqual([
      { i: 0, text: "10-14" },
      { i: 2, text: "10-16" },
    ]);
  });

  it("fills land on the candle they happened in", () => {
    const list = ["09:15", "09:16", "09:20"].map((t) => candle(at(t), 1));
    const mark = (t: string, side: "BUY" | "SELL" = "BUY"): ChartMark => ({ t, side, qty: 75, price_paise: 1, client_order_id: t, strategy: "manual" });
    const placed = marksByCandle(list, [
      mark("2026-10-16T03:45:30+00:00"), // 09:15:30 India
      mark("2026-10-16T03:48:00+00:00", "SELL"), // 09:18: the 09:16 candle (no 09:17..09:19 candles)
      mark("2026-10-16T03:50:59+00:00"), // 09:20:59
      mark("2026-10-16T03:44:00+00:00"), // before the first candle: not shown
    ]);
    expect([...placed.entries()].map(([i, m]) => [i, m.map((x) => x.side)])).toEqual([
      [0, ["BUY"]],
      [1, ["SELL"]],
      [2, ["BUY"]],
    ]);
    expect(marksByCandle([], [mark("2026-10-16T03:45:30+00:00")]).size).toBe(0);
  });
});
