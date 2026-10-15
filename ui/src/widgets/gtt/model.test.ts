import { describe, expect, it } from "vitest";
import type { GttSpec, GttView } from "../../api/types";
import { buildGttBody, defaultLegs, gttTone, legsText, sortGtts } from "./model";

const SPEC: GttSpec = {
  instrument_id: "NFO:NIFTY26OCTFUT",
  kinds: ["single", "two-leg"],
  legs: { single: 1, "two-leg": 2 },
  labels: { single: "Single", "two-leg": "OCO (stop + target)" },
  tick_paise: 10,
  lot_size: 75,
  products: ["NRML", "MIS"],
  min_distance_pct: 0.25,
  expiry_days: 365,
  max_active: 250,
  help: {},
};

function gtt(id: string, extra: Partial<GttView> = {}): GttView {
  return {
    gtt_id: id,
    account_id: "acc",
    instrument_id: SPEC.instrument_id,
    product: "NRML",
    kind: "single",
    legs: [{ trigger_paise: 2480000, side: "SELL", qty: 75, price_paise: 2479950 }],
    status: "active",
    order_id: null,
    fired_leg: null,
    reason: "",
    created_at: null,
    updated_at: "2026-10-15T10:00:00Z",
    ...extra,
  };
}

describe("GTT form", () => {
  it("starts a sell stop 1% below the market, its limit five ticks past the trigger", () => {
    expect(defaultLegs(SPEC, "single", 2501000, "SELL")).toEqual([{ trigger: "24,759.90", price: "24,759.40", side: "SELL" }]);
    expect(defaultLegs(SPEC, "single", 2501000, "BUY")[0]).toEqual({ trigger: "25,260.10", price: "25,260.60", side: "BUY" });
  });

  it("an OCO starts with a stop below and a target above, as many legs as the spec says", () => {
    const legs = defaultLegs(SPEC, "two-leg", 2501000, "SELL");
    expect(legs.map((l) => l.trigger)).toEqual(["24,759.90", "25,260.10"]);
    expect(defaultLegs({ ...SPEC, legs: { single: 2, "two-leg": 2 } }, "single", 2501000, "SELL")).toHaveLength(2); // the spec decides
    expect(defaultLegs(SPEC, "single", null, "SELL")).toEqual([{ trigger: "", price: "", side: "SELL" }]);
  });

  it("builds the request in paise with whole lots", () => {
    const { body, problems } = buildGttBody({
      spec: SPEC,
      kind: "single",
      legs: [{ trigger: "24,800", price: "24,780.50", side: "SELL" }],
      lots: 2,
      product: "NRML",
      ltp: 2501000,
    });
    expect(problems).toEqual([]);
    expect(body).toEqual({
      instrument_id: SPEC.instrument_id,
      product: "NRML",
      kind: "single",
      legs: [{ trigger_paise: 2480000, price_paise: 2478050, side: "SELL", qty: 150 }],
    });
  });

  it("says every problem before the click", () => {
    const { body, problems } = buildGttBody({
      spec: SPEC,
      kind: "weird",
      legs: [
        { trigger: "25,000", price: "x", side: "SELL" },
        { trigger: "24,000.05", price: "24,000", side: "SELL" },
      ],
      lots: 0,
      product: "NRML",
      ltp: 2501000,
    });
    expect(body).toBeNull();
    expect(problems).toEqual([
      "weird GTTs are not offered here",
      "Lots must be a whole number above 0",
      "Leg 1: the price isn't a price",
      "Leg 1: the trigger is only 0.04% from the last price (at least 0.25%)",
      "Leg 2: the trigger must be on the 0.10 grid",
    ]);
    const swapped = buildGttBody({
      spec: SPEC,
      kind: "two-leg",
      legs: [
        { trigger: "25,300", price: "25,290", side: "SELL" },
        { trigger: "24,800", price: "24,790", side: "SELL" },
      ],
      lots: 1,
      product: "NRML",
      ltp: 2501000,
    });
    expect(swapped.problems).toEqual(["Leg 1 is the lower trigger (the stop), leg 2 the higher (the target)"]);
  });

  it("lists active GTTs first, newest first, and tones them by status", () => {
    const list = sortGtts({
      a: gtt("a", { status: "deleted", updated_at: "2026-10-15T12:00:00Z" }),
      b: gtt("b", { updated_at: "2026-10-15T09:00:00Z" }),
      c: gtt("c", { updated_at: "2026-10-15T11:00:00Z" }),
    });
    expect(list.map((g) => g.gtt_id)).toEqual(["c", "b", "a"]);
    expect([gtt("x"), gtt("y", { status: "triggered", order_id: "9" }), gtt("z", { status: "rejected" }), gtt("w", { status: "deleted" })].map(gttTone)).toEqual([
      "buy",
      "good",
      "bad",
      "dim",
    ]);
    expect(legsText(gtt("x"))).toBe("SELL 75 @ 24,799.50 when 24,800.00");
  });
});
