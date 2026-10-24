import { anyGoing, buildBacktestBody, bytesText, compareSeries, dayUrl, nearest, offeredDays, plot, runName, runState, togglePick, winRate, type BacktestInput } from "./model";
import { FORM, FUT, run, summary } from "./testdata";

function input(extra: Partial<BacktestInput> = {}): BacktestInput {
  return {
    form: FORM,
    algo: "SLICER",
    values: { slices: "4", mode: "cross" },
    instrumentId: FUT,
    side: "SELL",
    lots: 2,
    lotSize: 75,
    product: "NRML",
    days: ["2026-10-16", "2026-10-14"],
    start: "09:20",
    capital: "5,00,000",
    name: "  fast slicer ",
    candles: false,
    ...extra,
  };
}

describe("research model", () => {
  it("sizes read like people say them", () => {
    expect(bytesText(0)).toBe("0 B");
    expect(bytesText(1023)).toBe("1023 B");
    expect(bytesText(1536)).toBe("1.5 KB");
    expect(bytesText(10 * 1024)).toBe("10 KB");
    expect(bytesText(1024 * 1024 * 3.25)).toBe("3.3 MB");
    expect(bytesText(1024 ** 3 * 42)).toBe("42 GB");
    expect(bytesText(1024 ** 5)).toBe("1024 TB"); // the largest unit stays
  });

  it("a run's name and state in words", () => {
    expect(runName(run("BT1"))).toBe("SLICER BUY 150 NIFTY26OCTFUT");
    expect(runName(run("BT1", { spec: { ...run("BT1").spec, name: "mine" } }))).toBe("mine");
    expect(runState(run("BT1", { state: "queued", summary: undefined }))).toEqual({ text: "Waiting for the run before it", tone: "pending" });
    expect(runState(run("BT1", { state: "running", progress: 1, days: 5 }))).toEqual({ text: "Running: 1 of 5 days", tone: "pending" });
    expect(runState(run("BT1", { state: "failed", error: "boom" }))).toEqual({ text: "Failed: boom", tone: "bad" });
    expect(runState(run("BT1"))).toEqual({ text: "Done", tone: "good" });
    expect(runState(run("BT1", { state: "cancelled", summary: undefined }))).toEqual({ text: "Cancelled", tone: "dim" });
    expect(anyGoing([run("A", { state: "cancelled", summary: undefined })])).toBe(false);
    expect(runState(run("BT1", { summary: summary({ days_skipped: ["a"] }) })).text).toBe("Done (1 day skipped)");
    expect(runState(run("BT1", { summary: summary({ days_skipped: ["a", "b"] }) })).text).toBe("Done (2 days skipped)");
    expect(anyGoing([run("A"), run("B", { state: "failed" })])).toBe(false);
    expect(anyGoing([run("A"), run("B", { state: "queued" })])).toBe(true);
    expect(anyGoing([run("A", { state: "running" })])).toBe(true);
    expect(winRate(summary({ win_rate: 2 / 3 }))).toBe("66.7%");
    expect(winRate(summary({ win_rate: null }))).toBe("–");
    expect(dayUrl("BT 1", "2026-10-16", "pdf")).toBe("/api/research/backtests/BT%201/days/2026-10-16?format=pdf");
  });

  it("the request is built from the form: days in order, lots to quantity, rupees to paise", () => {
    const { body, problems } = buildBacktestBody(input());
    expect(problems).toEqual([]);
    expect(body).toEqual({
      days: ["2026-10-14", "2026-10-16"],
      algo: "SLICER",
      instrument_id: FUT,
      side: "SELL",
      qty: 150,
      params: { slices: 4, mode: "cross" },
      product: "NRML",
      start: "09:20",
      capital_paise: 50000000,
      name: "fast slicer",
      candles: false,
    });
    expect(buildBacktestBody(input({ product: "" })).body!.product).toBe("NRML"); // the server's default
  });

  it("every problem is listed and nothing is built", () => {
    const { body, problems } = buildBacktestBody(input({ instrumentId: null, days: [], start: "9:20", capital: "0", values: { slices: "", mode: "cross" } }));
    expect(body).toBeNull();
    expect(problems).toEqual(["Pick a contract", "Slices is required", "Pick at least one day", "Start must be a time like 09:20", "Capital must be an amount above ₹0"]);
    expect(buildBacktestBody(input({ days: ["2026-10-14", "2026-10-15", "2026-10-16"] })).problems).toEqual(["At most 2 days in one run"]);
    expect(buildBacktestBody(input({ start: "24:00" })).problems).toEqual(["Start must be a time like 09:20"]);
    expect(buildBacktestBody(input({ start: "23:59" })).problems).toEqual([]);
    expect(buildBacktestBody(input({ capital: "abc" })).problems).toEqual(["Capital must be an amount above ₹0"]);
    expect(buildBacktestBody(input({ algo: "NOPE" })).problems).toEqual(["Pick an algo"]);
    expect(buildBacktestBody(input({ lots: 0 })).problems).toEqual(["Lots must be at least 1"]);
  });

  it("the days offered: recorded, or the contract's candle days", () => {
    expect(offeredDays(null, false)).toEqual([]);
    expect(offeredDays(FORM, false)).toEqual(FORM.days);
    expect(offeredDays(FORM, true)).toEqual([]);
    expect(offeredDays({ ...FORM, candle_days: ["2026-10-13"] }, true)).toEqual(["2026-10-13"]);
  });

  it("the plot always shows zero, spreads a flat line and lifts the pen over gaps", () => {
    const p = plot([{ key: "a", label: "a", points: [100, null, 300, 200] }], 4, 100, 50, 0);
    expect([p.min, p.max]).toEqual([0, 300]);
    expect(p.zero).toBe(50);
    expect(p.y(300)).toBe(0);
    expect(p.x(3)).toBe(100);
    expect(p.paths[0]).toBe("M0.0,33.3M66.7,0.0L100.0,16.7");
    const losses = plot([{ key: "a", label: "a", points: [-200, -100] }], 2, 100, 50, 0);
    expect([losses.min, losses.max, losses.zero]).toEqual([-200, 0, 0]);
    const flat = plot([{ key: "a", label: "a", points: [0, 0] }], 2, 100, 50, 0);
    expect([flat.min, flat.max, flat.zero]).toEqual([-1, 1, 25]);
    const one = plot([{ key: "a", label: "a", points: [500] }], 1, 100, 50, 0);
    expect(one.x(0)).toBe(50);
    expect(plot([{ key: "a", label: "a", points: [null] }], 1, 100, 50).paths).toEqual([""]);
  });

  it("the hover finds the nearest point", () => {
    expect(nearest(0, 5, 100, 0)).toBe(0);
    expect(nearest(12, 5, 100, 0)).toBe(0);
    expect(nearest(13, 5, 100, 0)).toBe(1);
    expect(nearest(500, 5, 100, 0)).toBe(4);
    expect(nearest(-5, 5, 100, 0)).toBe(0);
    expect(nearest(70, 1, 100, 0)).toBe(0);
  });

  it("compared runs share the days any of them ran, with gaps where one didn't", () => {
    const a = run("A");
    const b = run("B", { summary: summary({ equity_curve: [["2026-10-15", 100], ["2026-10-16", 300]] }) });
    const { days, series } = compareSeries([
      { run: a, slot: 2 },
      { run: b, slot: 0 },
    ]);
    expect(days).toEqual(["2026-10-14", "2026-10-15", "2026-10-16"]);
    expect(series.map((s) => [s.key, s.slot, s.points])).toEqual([
      ["A", 2, [-5000, 40000, null]],
      ["B", 0, [null, 100, 300]],
    ]);
  });

  it("a compared run keeps its colour slot while others come and go", () => {
    let s = togglePick([], "A");
    s = togglePick(s, "B");
    s = togglePick(s, "C");
    expect(s).toEqual(["A", "B", "C", null]);
    s = togglePick(s, "A"); // A goes: B and C keep their colours
    expect(s).toEqual([null, "B", "C", null]);
    s = togglePick(s, "D"); // the lowest free slot
    s = togglePick(s, "E");
    expect(s).toEqual(["D", "B", "C", "E"]);
    expect(togglePick(s, "F")).toEqual(s); // full: nothing more
  });
});
