import type { Print } from "../../api/types";
import { tick } from "../../test/fakes";
import { TAPE_MAX, depth, filterTape, joinTape } from "./model";

const FUT = "NFO:NIFTY26OCTFUT";
const p = (seq: number, qty = 75, side: Print["side"] = "BUY"): Print => ({ seq, t: null, price_paise: 100, qty, side });

describe("market screens model", () => {
  it("depth: bars against the largest level, running totals, the book, the imbalance and the spread", () => {
    const t = tick(FUT, 2501000, {
      bids: [[2500990, 150, 2], [2500980, 600, 4], [2500970, 75, 1]],
      asks: [[2501010, 300, 3], [2501020, 75, 1]],
      buy_qty: 9000,
      sell_qty: 3000,
    });
    const d = depth(t, 10)!;
    expect(d.bids.map((l) => [l.qty, l.cumulative, l.share])).toEqual([[150, 150, 0.25], [600, 750, 1], [75, 825, 0.125]]);
    expect(d.asks.map((l) => [l.qty, l.cumulative, l.share])).toEqual([[300, 300, 0.5], [75, 375, 0.125]]);
    expect([d.bidTotal, d.askTotal, d.bookBid, d.bookAsk]).toEqual([825, 375, 9000, 3000]);
    expect(d.imbalance).toBe(0.75); // the whole book: 9,000 of 12,000
    expect(d.spread).toEqual({ paise: 20, ticks: 2 });
    const shown = depth(tick(FUT, 1, { bids: [[100, 30, 1]], asks: [[110, 10, 1]] }), 10)!;
    expect(shown.imbalance).toBe(0.75); // no book totals sent: the levels shown
    const empty = depth(tick(FUT, 1), 10)!;
    expect([empty.imbalance, empty.spread, empty.bidTotal]).toEqual([null, null, 0]);
    expect(depth(null, 10)).toBeNull();
  });

  it("the tape: new prints on top, never twice, at most TAPE_MAX", () => {
    const tape = joinTape([], [p(1), p(2), p(3)]);
    expect(tape.map((x) => x.seq)).toEqual([3, 2, 1]);
    const again = joinTape(tape, [p(2), p(3), p(4)]);
    expect(again.map((x) => x.seq)).toEqual([4, 3, 2, 1]);
    expect(joinTape(again, [])).toBe(again);
    const long = joinTape([], Array.from({ length: TAPE_MAX + 20 }, (_, k) => p(k + 1)));
    expect(long).toHaveLength(TAPE_MAX);
    expect(long[0]!.seq).toBe(TAPE_MAX + 20);
  });

  it("the size filter and the buy/sell totals of what is shown", () => {
    const tape = [p(4, 750, "SELL"), p(3, 75, "BUY"), p(2, 1500, "BUY"), p(1, 900, "")];
    expect(filterTape(tape, 0)).toEqual({ rows: tape, bought: 1575, sold: 750 });
    const big = filterTape(tape, 750);
    expect(big.rows.map((x) => x.seq)).toEqual([4, 2, 1]);
    expect([big.bought, big.sold]).toEqual([1500, 750]);
  });
});
