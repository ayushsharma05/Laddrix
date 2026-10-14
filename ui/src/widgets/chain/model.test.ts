import type { ChainLeg, ChainRow, OptionChain } from "../../api/types";
import { cell, COLUMNS, expiryLabel, inTheMoney, maxOi, pickColumns, strikeText } from "./model";

const leg = (extra: Partial<ChainLeg> = {}): ChainLeg => ({
  instrument_id: "NFO:X",
  ltp_paise: 22750,
  bid_paise: 22745,
  ask_paise: 22755,
  volume: 123456,
  oi: 82050,
  oi_change: -1500,
  iv: 0.12,
  delta: 0.5103,
  gamma: 0.0004,
  theta: -12.34,
  vega: 9.87,
  ...extra,
});
const col = (key: string) => COLUMNS.find((c) => c.key === key)!;

describe("option chain model", () => {
  it("formats each column the way traders read it", () => {
    const l = leg();
    expect(["oi", "oi_change", "volume", "iv", "delta", "theta", "vega", "ltp"].map((k) => cell(l, col(k)))).toEqual([
      "82.05K",
      "−1.5K",
      "1.23L",
      "12.00",
      "0.51",
      "-12.34",
      "9.87",
      "227.50",
    ]);
    expect(cell(leg({ oi_change: 2000 }), col("oi_change"))).toBe("+2K");
  });

  it("shows – for a missing value, never 0; nothing for a missing leg", () => {
    expect(cell(leg({ iv: null, delta: null }), col("iv"))).toBe("–");
    expect(cell(null, col("iv"))).toBe("");
  });

  it("keeps the user's columns in their order and ignores unknown ones", () => {
    expect(pickColumns(["ltp", "nonsense", "iv"]).map((c) => c.key)).toEqual(["ltp", "iv"]);
    expect(pickColumns([]).map((c) => c.key)).toEqual(["oi", "oi_change", "iv", "delta", "ltp"]);
  });

  it("calls are in the money below the forward, puts above it", () => {
    const row = (k: number): ChainRow => ({ strike_paise: k, call: leg(), put: leg() });
    expect(inTheMoney(row(2490000), "call", 2500995)).toBe(true);
    expect(inTheMoney(row(2490000), "put", 2500995)).toBe(false);
    expect(inTheMoney(row(2510000), "put", 2500995)).toBe(true);
    expect(inTheMoney(row(2510000), "call", null)).toBe(false);
  });

  it("OI bars scale to the largest OI on each side", () => {
    const chain = { rows: [{ strike_paise: 1, call: leg({ oi: 10 }), put: leg({ oi: 40 }) }, { strike_paise: 2, call: leg({ oi: 30 }), put: null }] } as OptionChain;
    expect(maxOi(chain)).toEqual({ call: 30, put: 40 });
  });

  it("labels", () => {
    expect(expiryLabel("2026-10-27")).toBe("27 Oct");
    expect(strikeText(2500000)).toBe("25,000");
    expect(strikeText(2500050)).toBe("25,000.50");
  });
});
