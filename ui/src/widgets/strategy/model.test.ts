import type { StrategyAnalysis, StrategyLegIn } from "../../api/types";
import { addLeg, chart, flip, money, premiumText, removeLeg, setLots } from "./model";

const A: StrategyAnalysis = {
  underlying: "NIFTY",
  expiry: "2026-10-27",
  forward_paise: 2500000,
  prices: [24800, 24900, 25000, 25100, 25200],
  payoff_expiry: [-4500, 3000, 3000, 3000, -4500],
  payoff_today: [-2000, 1000, 1500, 1000, -2000],
  breakevens: [24860, 25140, 30000],
  max_profit: 3000,
  max_loss: -4500,
  net_premium: 3000,
  net_greeks: { delta: -0.54, gamma: -0.001, theta: 446.8, vega: -120 },
  legs: [],
};

describe("strategy model", () => {
  it("scales the server's curves into the box, zero line included", () => {
    const c = chart(A, { width: 400, height: 200, pad: 20 });
    expect(c.xMin).toBe(24800);
    expect(c.xMax).toBe(25200);
    expect(c.forwardX).toBe(200); // 25,000 is the middle
    expect(c.expiryPath.startsWith("M20.0,")).toBe(true);
    expect(c.expiryPath.split("L")).toHaveLength(5);
    expect(c.yMin).toBeLessThan(-4500);
    expect(c.yMax).toBeGreaterThan(3000);
    expect(c.zeroY).toBeGreaterThan(20);
    expect(c.zeroY).toBeLessThan(180);
    expect(c.breakevenXs).toHaveLength(2); // 30,000 is off the chart
  });

  it("says money the way a trader reads it", () => {
    expect(money(7196.25)).toBe("+₹7,196");
    expect(money(-7803.75)).toBe("−₹7,804");
    expect(money("unlimited")).toBe("Unlimited");
    expect(money(123456, false)).toBe("₹1,23,456");
    expect(premiumText(3000)).toBe("Credit ₹3,000");
    expect(premiumText(-6251.25)).toBe("Debit ₹6,251");
    expect(premiumText(0.2)).toBe("No net premium");
  });

  it("edits legs: flip, lots within 1-100, remove, add (merging the same contract and side)", () => {
    const legs: StrategyLegIn[] = [{ instrument_id: "A", side: "BUY", lots: 1, price_paise: 2000 }];
    expect(flip(legs, 0)).toEqual([{ instrument_id: "A", side: "SELL", lots: 1, price_paise: undefined }]);
    expect(setLots(legs, 0, 0)[0]!.lots).toBe(1);
    expect(setLots(legs, 0, 500)[0]!.lots).toBe(100);
    expect(removeLeg(legs, 0)).toEqual([]);
    expect(addLeg(legs, { instrument_id: "A", side: "BUY", lots: 2 })[0]!.lots).toBe(3);
    expect(addLeg(legs, { instrument_id: "A", side: "SELL", lots: 1 })).toHaveLength(2);
    const eight = Array.from({ length: 8 }, (_, i) => ({ instrument_id: `X${i}`, side: "BUY" as const, lots: 1 }));
    expect(addLeg(eight, { instrument_id: "Y", side: "BUY", lots: 1 })).toHaveLength(8);
  });
});
