import { order } from "../../test/fixtures";
import { tick } from "../../test/fakes";
import type { Instrument, OrderView } from "../../api/types";
import {
  buildOrder,
  buildRows,
  needsRecentre,
  newClientOrderId,
  orderValue,
  productsFor,
  QueueTracker,
  queueAt,
  rowPrices,
  snap,
  unconfirmed,
  VapCounter,
  workingOrders,
} from "./model";

const ID = "NFO:NIFTY26OCTFUT";
const NIFTY: Instrument = {
  id: ID,
  exchange: "NFO",
  symbol: "NIFTY26OCTFUT",
  kind: "FUT",
  tick_paise: 10,
  lot_size: 75,
  expiry: "2026-10-27",
  strike_paise: null,
  underlying: "NIFTY",
  tradable: true,
};

// best bid 25,010.00 × 150, best ask 25,010.10 × 75, then thinner levels
const BOOK = tick(ID, 2501000, {
  volume: 1000,
  last_qty: 75,
  bids: [
    [2501000, 150, 2],
    [2500990, 300, 3],
    [2500970, 75, 1], // 25,009.80 is a gap: nobody there
  ],
  asks: [
    [2501010, 75, 1],
    [2501020, 225, 2],
  ],
});

function working(id: string, side: "BUY" | "SELL", price: number, qty = 75, extra: Partial<OrderView> = {}): OrderView {
  const o = order(id, "OPEN", "2026-10-14T04:40:01+00:00", extra);
  return { ...o, request: { ...o.request, side, price_paise: price, qty } };
}

describe("the price grid", () => {
  it("snaps to the tick size", () => {
    expect(snap(2501007, 10)).toBe(2501010);
    expect(snap(2501004, 10)).toBe(2501000);
    expect(snap(10002, 5)).toBe(10000);
    expect(snap(10003, 5)).toBe(10005);
  });

  it("lays rows out highest first, every row on the grid, the centre in the middle", () => {
    const rows = rowPrices(2501007, 5, 10);
    expect(rows).toEqual([2501030, 2501020, 2501010, 2501000, 2500990]);
    for (const p of rowPrices(2501000, 31, 5)) expect(p % 5).toBe(0);
    expect(rowPrices(2501000, 30, 10)).toHaveLength(30);
  });

  it("re-centres only when the last price leaves the middle half", () => {
    expect(needsRecentre(2501000, 2501060, 30, 10)).toBe(false); // 6 rows away of 30
    expect(needsRecentre(2501000, 2501080, 30, 10)).toBe(true); // 8 rows away
    expect(needsRecentre(2501000, 2500920, 30, 10)).toBe(true);
  });
});

describe("working orders", () => {
  it("are open, partial or pending-new orders with something left, on this instrument", () => {
    const orders: Record<string, OrderView> = {
      a: working("a", "BUY", 2501000),
      b: { ...working("b", "BUY", 2501000), status: "FILLED", filled_qty: 75 },
      c: { ...working("c", "SELL", 2501020), status: "PARTIAL", filled_qty: 25 },
      d: { ...working("d", "BUY", 2501000), request: { ...working("d", "BUY", 2501000).request, instrument_id: "NSE:SBIN" } },
      e: { ...working("e", "BUY", 2500990), status: "PENDING" },
      f: { ...working("f", "BUY", 2500990), status: "CANCELLED" },
    };
    expect(workingOrders(orders, ID).map((o) => o.request.client_order_id).sort()).toEqual(["a", "c", "e"]);
  });

  it("unconfirmed ones are found so nothing more is sent on that contract", () => {
    const orders = { a: working("a", "BUY", 2501000, 75, { pending: "UNCONFIRMED" }), b: working("b", "BUY", 2501000) };
    expect(unconfirmed(orders, ID).map((o) => o.request.client_order_id)).toEqual(["a"]);
    expect(unconfirmed(orders, "NSE:SBIN")).toEqual([]);
  });
});

describe("queue ahead (PIQ)", () => {
  it("joining a depth level puts everyone shown there ahead", () => {
    expect(queueAt("BUY", 2501000, BOOK)).toBe(150);
    expect(queueAt("SELL", 2501020, BOOK)).toBe(225);
  });

  it("improving on the best price, or a gap inside the depth, has nobody ahead", () => {
    expect(queueAt("BUY", 2501010, BOOK)).toBe(0); // above the best bid (it would trade, but no queue)
    expect(queueAt("BUY", 2500980, BOOK)).toBe(0); // the empty 25,009.80 inside the depth
    expect(queueAt("SELL", 2501000, BOOK)).toBe(0);
  });

  it("beyond the 5 levels we can see, it's unknown", () => {
    expect(queueAt("BUY", 2500900, BOOK)).toBeNull();
    expect(queueAt("SELL", 2501100, BOOK)).toBeNull();
    expect(queueAt("BUY", 2501000, null)).toBeNull();
  });

  it("counts down as trades happen at my price, and never below zero", () => {
    const q = new QueueTracker();
    q.update(BOOK, []);
    q.joined("o1", "BUY", 2501000, BOOK); // 150 ahead
    const mine = [working("o1", "BUY", 2501000)];
    // my 75 now shows in the depth: 225 at 25,010.00
    const t1 = { ...BOOK, volume: 1100, bids: [[2501000, 225, 3] as [number, number, number]] };
    q.update(t1, mine);
    expect(q.ahead("o1")).toBe(50); // 100 traded at my price
    const t2 = { ...t1, volume: 1300, bids: [[2501000, 75, 1] as [number, number, number]] };
    q.update(t2, mine);
    expect(q.ahead("o1")).toBe(0);
  });

  it("trades at another price don't move my place", () => {
    const q = new QueueTracker();
    q.update(BOOK, []);
    q.joined("o1", "BUY", 2500990, BOOK); // 300 ahead
    const mine = [working("o1", "BUY", 2500990)];
    q.update({ ...BOOK, volume: 1500, bids: [[2501000, 150, 2], [2500990, 375, 4]] }, mine); // 500 traded at 25,010.00
    expect(q.ahead("o1")).toBe(300);
  });

  it("cancels ahead of me shorten the queue: never longer than the depth shown minus me", () => {
    const q = new QueueTracker();
    q.update(BOOK, []);
    q.joined("o1", "BUY", 2500990, BOOK); // 300 ahead
    const mine = [working("o1", "BUY", 2500990)];
    q.update({ ...BOOK, bids: [[2501000, 150, 2], [2500990, 150, 2]] }, mine); // 75 others + my 75
    expect(q.ahead("o1")).toBe(75);
  });

  it("an order found later is assumed at the back of the queue", () => {
    const q = new QueueTracker();
    q.update({ ...BOOK, bids: [[2501000, 225, 3]] }, [working("o1", "BUY", 2501000)]);
    expect(q.ahead("o1")).toBe(150); // 225 shown minus my 75
  });

  it("a moved order goes to the back of the queue at the new price", () => {
    const q = new QueueTracker();
    q.update(BOOK, []);
    q.joined("o1", "BUY", 2501000, BOOK);
    q.update(BOOK, [working("o1", "BUY", 2501000)]);
    q.update({ ...BOOK, bids: [[2501000, 150, 2], [2500990, 375, 4]] }, [working("o1", "BUY", 2500990)]);
    expect(q.ahead("o1")).toBe(300);
  });

  it("forgets finished orders, and orders that never showed up", () => {
    const q = new QueueTracker();
    q.update(BOOK, []);
    q.joined("o1", "BUY", 2501000, BOOK);
    q.joined("never", "BUY", 2501000, BOOK);
    q.update(BOOK, [working("o1", "BUY", 2501000)]);
    q.update(BOOK, []); // o1 filled or cancelled
    expect(q.ahead("o1")).toBeNull();
    for (let i = 0; i < 40; i++) q.update(BOOK, []);
    expect(q.size).toBe(0);
  });
});

describe("volume at price", () => {
  it("starts from the server's count and adds only later volume, at the last price", () => {
    const vap = new VapCounter();
    vap.reset(1000, [[2501000, 600]]);
    vap.onTick({ ...BOOK, volume: 900 + 100 }); // not newer than the server's count: nothing
    vap.onTick({ ...BOOK, ltp_paise: 2501010, volume: 1150 });
    vap.onTick({ ...BOOK, volume: 1225 });
    expect(vap.at(2501000)).toBe(675);
    expect(vap.at(2501010)).toBe(150);
    expect(vap.max()).toBe(675);
  });

  it("without a server count, the first tick is only a baseline", () => {
    const vap = new VapCounter();
    vap.onTick({ ...BOOK, volume: 1000 });
    vap.onTick({ ...BOOK, volume: 1075 });
    expect(vap.at(2501000)).toBe(75);
  });

  it("volume going backwards is a new day", () => {
    const vap = new VapCounter();
    vap.reset(1000, [[2501000, 600]]);
    vap.onTick({ ...BOOK, volume: 50 });
    expect(vap.at(2501000)).toBe(0);
    vap.onTick({ ...BOOK, volume: 125 });
    expect(vap.at(2501000)).toBe(75);
  });
});

describe("rows", () => {
  it("show depth, best levels, last price, my orders, PIQ, LTQ and VAP on the right rows", () => {
    const q = new QueueTracker();
    q.update(BOOK, []);
    q.joined("b1", "BUY", 2500990, BOOK);
    const vap = new VapCounter();
    vap.reset(1000, [[2501000, 600], [2500990, 300]]);
    const mine = [
      working("b1", "BUY", 2500990, 150),
      working("b2", "BUY", 2500990, 75, { pending: "CANCEL" }),
      working("s1", "SELL", 2501020, 75),
    ];
    const rows = buildRows({ prices: rowPrices(2501000, 7, 10), tick: BOOK, working: mine, queue: q, vap });
    const at = (p: number) => rows.find((r) => r.price === p)!;

    expect(rows.map((r) => r.price)).toEqual([2501030, 2501020, 2501010, 2501000, 2500990, 2500980, 2500970]);
    expect(at(2501000)).toMatchObject({ bid: 150, ask: null, bestBid: true, last: true, ltq: 75, vap: 600, vapShare: 100 });
    expect(at(2501010)).toMatchObject({ bid: null, ask: 75, bestAsk: true, last: false, ltq: null });
    expect(at(2500980)).toMatchObject({ bid: null, ask: null }); // the gap
    expect(at(2500990)).toMatchObject({ workBuy: 225, workSell: 0, pendingCancel: true, piq: 300, vapShare: 50 });
    expect(at(2501020)).toMatchObject({ workBuy: 0, workSell: 75, pendingCancel: false });
  });

  it("market orders (no price) appear on no row", () => {
    const m = working("m1", "BUY", 2501000);
    const market = { ...m, request: { ...m.request, order_type: "MARKET" as const, price_paise: null } };
    const rows = buildRows({ prices: [2501000], tick: BOOK, working: [market], queue: new QueueTracker(), vap: new VapCounter() });
    expect(rows[0]!.workBuy).toBe(0);
  });
});

describe("building an order", () => {
  it("a limit: lots × lot size at the row's price, on the grid", () => {
    const body = buildOrder({ instrument: NIFTY, side: "BUY", lots: 2, price: 2501000, type: "LMT", tif: "DAY", product: "NRML", clientOrderId: "X1" });
    expect(body).toEqual({
      client_order_id: "X1",
      instrument_id: ID,
      side: "BUY",
      qty: 150,
      order_type: "LIMIT",
      product: "NRML",
      validity: "DAY",
      price_paise: 2501000,
      trigger_paise: null,
      market_protection: null,
    });
    expect(orderValue(body, 2501000)).toBe(150 * 2501000);
  });

  it("a market order has no price and always carries market protection", () => {
    const body = buildOrder({ instrument: NIFTY, side: "SELL", lots: 1, price: 2501000, type: "MKT", tif: "IOC", product: "MIS" });
    expect(body).toMatchObject({ order_type: "MARKET", price_paise: null, market_protection: -1, validity: "IOC", qty: 75 });
    expect(orderValue(body, 2501010)).toBe(75 * 2501010);
  });

  it("client order ids are 1-20 letters or digits and don't repeat", () => {
    const ids = new Set<string>();
    for (let i = 0; i < 500; i++) {
      const id = newClientOrderId(1792000000000, () => 0.5);
      expect(id).toMatch(/^[A-Za-z0-9]{1,20}$/);
      ids.add(id);
    }
    expect(ids.size).toBe(500);
  });

  it("offers the products that fit the instrument", () => {
    expect(productsFor({ kind: "EQ" })).toEqual(["CNC", "MIS"]);
    expect(productsFor({ kind: "FUT" })).toEqual(["NRML", "MIS"]);
    expect(productsFor({ kind: "CE" })).toEqual(["NRML", "MIS"]);
  });
});
