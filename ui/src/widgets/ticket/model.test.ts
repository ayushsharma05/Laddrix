import { marginText } from "./useChargeEstimate";
import type { OrderTypeSpec } from "../../api/types";
import { orderSpec } from "../../test/fixtures";
import { buildTicketOrder, parsePrice, paysAtMost, pickType, pickValidity, stepPrice, summarise, ticketProblem, type TicketInput } from "./model";

const SPEC = orderSpec();
const ID = SPEC.instrument_id;
const LTP = 2501000;
const TYPE = (label: string): OrderTypeSpec => SPEC.order_types.find((x) => x.label === label)!;

type Change = Omit<Partial<TicketInput>, "type"> & { type?: string };
const t = ({ type = "LMT", ...extra }: Change = {}): TicketInput => ({
  spec: SPEC, type: TYPE(type), symbol: "NIFTY26OCTFUT", side: "BUY", lots: 2, price: 2501000, trigger: null, validity: "DAY", product: "NRML", ...extra,
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
    expect(buildTicketOrder(t(), ID, "T1")).toEqual({
      client_order_id: "T1", instrument_id: ID, side: "BUY", qty: 150, order_type: "LIMIT", product: "NRML",
      validity: "DAY", price_paise: 2501000, trigger_paise: null, market_protection: null,
    });
  });
  it("MKT: no price, automatic protection", () => {
    expect(buildTicketOrder(t({ type: "MKT", price: 2501000 }), ID)).toMatchObject({ order_type: "MARKET", price_paise: null, trigger_paise: null, market_protection: -1 });
  });
  it("SL: price and trigger", () => {
    expect(buildTicketOrder(t({ type: "SL", price: 2502000, trigger: 2501500 }), ID)).toMatchObject({ order_type: "SL", price_paise: 2502000, trigger_paise: 2501500, market_protection: null });
  });
  it("SL-M: trigger and protection, no price", () => {
    expect(buildTicketOrder(t({ type: "SL-M", side: "SELL", price: 2501000, trigger: 2500000 }), ID)).toMatchObject({ order_type: "SL-M", price_paise: null, trigger_paise: 2500000, market_protection: -1 });
  });
});

describe("problems are found before the click", () => {
  it("passes a good order of each type", () => {
    expect(ticketProblem(t(), LTP)).toBeNull();
    expect(ticketProblem(t({ type: "MKT", price: null }), LTP)).toBeNull();
    expect(ticketProblem(t({ type: "SL", price: 2502000, trigger: 2501500 }), LTP)).toBeNull();
    expect(ticketProblem(t({ type: "SL-M", side: "SELL", trigger: 2500000 }), LTP)).toBeNull();
    expect(ticketProblem(t({ spec: { ...SPEC, freeze_qty: 1800 }, lots: 24 }), LTP)).toBeNull(); // at the freeze: fine
    expect(ticketProblem(t({ spec: { ...SPEC, freeze_qty: null }, lots: 500 }), LTP)).toBeNull(); // none known
  });
  it.each([
    [{ lots: 0 }, "quantity is 0"],
    [{ price: null }, "enter a price"],
    [{ price: 2501005 }, "multiple of the tick"],
    [{ type: "SL", price: 2502000, trigger: null }, "enter a trigger"],
    [{ type: "SL", price: 2501500, trigger: 2502000 }, "must not be above the limit"],
    [{ type: "SL", side: "SELL", price: 2500500, trigger: 2500000 }, "must not be below the limit"],
    [{ type: "SL-M", trigger: 2500500 }, "above the last price"],
    [{ type: "SL-M", side: "SELL", trigger: 2501500 }, "below the last price"],
    [{ type: "SL-M", trigger: 2502000, validity: "IOC" }, "can't be IOC (allowed: DAY)"],
    [{ product: "CNC" }, "CNC isn't offered"],
    [{ spec: { ...SPEC, tradable: false } }, "can't be traded"],
    [{ spec: { ...SPEC, freeze_qty: 1800 }, lots: 25 }, "the exchange takes at most 1800 (24 lots) in one order: send fewer lots"],
  ] as [Change, string][])("%o → %s", (change, reason) => {
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
    expect(paysAtMost("BUY", 2501000, 10, 2)).toBe(2551020);
    expect(paysAtMost("SELL", 2501000, 10, 2)).toBe(2450980);
    const s = summarise(t({ type: "MKT" }), LTP, 0, 0);
    expect(s.worstPrice).toBe(2551020);
    expect(s.value).toBe(2551020 * 150);
  });
  it("stop-market is priced from its trigger; no last price means no distance", () => {
    expect(summarise(t({ type: "SL-M", trigger: 2502000 }), null, 0, 0)).toMatchObject({ worstPrice: paysAtMost("BUY", 2502000, 10, 2), distancePct: null });
  });
});

describe("the ticket follows the server's spec", () => {
  it("picks the saved type if still offered (by code or old label), else the first", () => {
    expect(pickType(SPEC, "SL-M")!.code).toBe("SL-M");
    expect(pickType(SPEC, "LMT")!.code).toBe("LIMIT"); // a layout saved before specs existed
    expect(pickType(SPEC, "ICEBERG")!.code).toBe("LIMIT");
    expect(pickType({ ...SPEC, order_types: [] }, "LIMIT")).toBeNull();
  });
  it("keeps a validity only if the type allows it", () => {
    expect(pickValidity(TYPE("LMT"), "IOC")).toBe("IOC");
    expect(pickValidity(TYPE("SL"), "IOC")).toBe("DAY");
  });
  it("a new kind of order from the server works without UI changes", () => {
    const iceberg: OrderTypeSpec = { ...TYPE("LMT"), code: "ICEBERG", label: "ICE", name: "Iceberg", validities: ["DAY"] };
    const body = buildTicketOrder({ ...t(), type: iceberg }, ID, "T9");
    expect(body).toMatchObject({ order_type: "ICEBERG", price_paise: 2501000, trigger_paise: null, market_protection: null });
  });
});


describe("margin line", () => {
  const base = { exit: false, required_paise: 100, available_paise: 500, enough: true, note: "", checked_before_sending: true };
  const fmt = (p: number) => `₹${p}`;
  it("says the margin, an exit, or why it is unknown", () => {
    expect(marginText(base, fmt)).toMatchObject({ text: "Margin ≈ ₹100 of ₹500", tone: "" });
    expect(marginText({ ...base, exit: true }, fmt)).toMatchObject({ text: "Margin: none (an exit)", tone: "dim" });
    expect(marginText({ ...base, required_paise: null, enough: null, note: "no margin quote within 1 s" }, fmt)).toMatchObject({ text: "Margin: –", title: "no margin quote within 1 s" });
    expect(marginText({ ...base, available_paise: null, enough: null }, fmt).text).toBe("Margin ≈ ₹100");
    expect(marginText({ ...base, enough: false, checked_before_sending: false }, fmt).title).toContain("the broker may reject it");
  });
});
