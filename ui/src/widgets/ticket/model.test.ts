import type { Instrument } from "../../api/types";
import { buildTicketOrder, parsePrice, paysAtMost, stepPrice, summarise, ticketProblem, type TicketInput } from "./model";

const NIFTY: Instrument = { id: "NFO:NIFTY26OCTFUT", exchange: "NFO", symbol: "NIFTY26OCTFUT", kind: "FUT", tick_paise: 10, lot_size: 75, expiry: "2026-10-27", strike_paise: null, underlying: "NIFTY", tradable: true };
const LTP = 2501000;

const t = (extra: Partial<TicketInput> = {}): TicketInput => ({
  instrument: NIFTY, side: "BUY", lots: 2, type: "LMT", price: 2501000, trigger: null, tif: "DAY", product: "NRML", ...extra,
});

describe("prices typed by hand", () => {
  it("parse rupees with or without commas into paise", () => {
    expect(parsePrice("25,010.10")).toBe(2501010);
    expect(parsePrice("25010.1")).toBe(2501010);
    expect(parsePrice("₹ 25010")).toBe(2501000);
    expect(parsePrice("0.05")).toBe(5);
  });
  it("refuse anything else", () => {
    for (const bad of ["", "abc", "25010.123", "-5", "0", "1e3", "25.010.10"]) expect(parsePrice(bad)).toBeNull();
  });
  it("−/+ move one tick on the grid, never below one tick", () => {
    expect(stepPrice(2501000, 10, 1)).toBe(2501010);
    expect(stepPrice(2501007, 10, -1)).toBe(2501000); // snaps first
    expect(stepPrice(10, 10, -5)).toBe(10);
  });
});

describe("each order type builds the right request", () => {
  it("LMT: a price, no trigger, no protection", () => {
    expect(buildTicketOrder(t(), "T1")).toEqual({
      client_order_id: "T1", instrument_id: NIFTY.id, side: "BUY", qty: 150, order_type: "LIMIT", product: "NRML",
      validity: "DAY", price_paise: 2501000, trigger_paise: null, market_protection: null,
    });
  });
  it("MKT: no price, automatic protection", () => {
    expect(buildTicketOrder(t({ type: "MKT", price: 2501000 }))).toMatchObject({ order_type: "MARKET", price_paise: null, trigger_paise: null, market_protection: -1 });
  });
  it("SL: price and trigger", () => {
    expect(buildTicketOrder(t({ type: "SL", price: 2502000, trigger: 2501500 }))).toMatchObject({ order_type: "SL", price_paise: 2502000, trigger_paise: 2501500, market_protection: null });
  });
  it("SL-M: trigger and protection, no price", () => {
    expect(buildTicketOrder(t({ type: "SL-M", side: "SELL", price: 2501000, trigger: 2500000 }))).toMatchObject({ order_type: "SL-M", price_paise: null, trigger_paise: 2500000, market_protection: -1 });
  });
});

describe("problems are found before the click", () => {
  it("passes a good order of each type", () => {
    expect(ticketProblem(t(), LTP)).toBeNull();
    expect(ticketProblem(t({ type: "MKT", price: null }), LTP)).toBeNull();
    expect(ticketProblem(t({ type: "SL", price: 2502000, trigger: 2501500 }), LTP)).toBeNull();
    expect(ticketProblem(t({ type: "SL-M", side: "SELL", trigger: 2500000 }), LTP)).toBeNull();
  });
  it.each([
    [{ lots: 0 }, "quantity is 0"],
    [{ price: null }, "enter a price"],
    [{ price: 2501005 }, "multiple of the tick"],
    [{ type: "SL" as const, price: 2502000, trigger: null }, "enter a trigger"],
    [{ type: "SL" as const, price: 2501500, trigger: 2502000 }, "must not be above the limit"],
    [{ type: "SL" as const, side: "SELL" as const, price: 2500500, trigger: 2500000 }, "must not be below the limit"],
    [{ type: "SL-M" as const, trigger: 2500500 }, "above the last price"],
    [{ type: "SL-M" as const, side: "SELL" as const, trigger: 2501500 }, "below the last price"],
    [{ type: "SL-M" as const, trigger: 2502000, tif: "IOC" as const }, "use DAY"],
    [{ instrument: { ...NIFTY, tradable: false } }, "can't be traded"],
  ])("%o → %s", (change, reason) => {
    expect(ticketProblem(t(change), LTP)).toContain(reason);
  });
});

describe("the summary line", () => {
  it("limit: value, worst-case position counting working orders, distance from last", () => {
    const s = summarise(t({ price: 2503500 }), LTP, 75, 150);
    expect(s.value).toBe(2503500 * 150);
    expect(s.worstPosition).toBe(75 + 150 + 150);
    expect(s.distancePct).toBeCloseTo(0.09996, 4);
    expect(s.worstPrice).toBeNull();
  });
  it("sell reduces the worst-case position", () => {
    expect(summarise(t({ side: "SELL" }), LTP, 150, 0).worstPosition).toBe(0);
  });
  it("market: priced at the protected worst, on the grid", () => {
    expect(paysAtMost("BUY", 2501000, 10)).toBe(2551020);
    expect(paysAtMost("SELL", 2501000, 10)).toBe(2450980);
    const s = summarise(t({ type: "MKT" }), LTP, 0, 0);
    expect(s.worstPrice).toBe(2551020);
    expect(s.value).toBe(2551020 * 150);
  });
  it("stop-market is priced from its trigger; no last price means no distance", () => {
    expect(summarise(t({ type: "SL-M", trigger: 2502000 }), null, 0, 0)).toMatchObject({ worstPrice: paysAtMost("BUY", 2502000, 10), distancePct: null });
  });
});
