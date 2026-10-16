// Market screens logic (K12.4): the depth window's numbers and the time & sales tape. Kept out of
// the widgets so it can be tested on its own; every number comes from the server's ticks.

import type { Print, Tick } from "../../api/types";

export interface DepthLevel {
  price: number;
  qty: number;
  orders: number;
  share: number; // of the largest quantity on either side (0..1), for the bar
  cumulative: number;
}

export interface Depth {
  bids: DepthLevel[]; // best first
  asks: DepthLevel[];
  bidTotal: number; // the levels shown
  askTotal: number;
  bookBid: number | null; // the whole book's waiting quantity, when the broker sends it
  bookAsk: number | null;
  imbalance: number | null; // bids' share of (bids + asks), from the whole book if sent
  spread: { paise: number; ticks: number } | null;
}

/** The depth window from one tick. */
export function depth(tick: Tick | null, tickPaise: number): Depth | null {
  if (!tick) return null;
  const most = Math.max(1, ...tick.bids.map((l) => l[1]), ...tick.asks.map((l) => l[1]));
  const side = (levels: [number, number, number][]): DepthLevel[] => {
    let sum = 0;
    return levels.map(([price, qty, orders]) => {
      sum += qty;
      return { price, qty, orders, share: qty / most, cumulative: sum };
    });
  };
  const bids = side(tick.bids);
  const asks = side(tick.asks);
  const bidTotal = bids.at(-1)?.cumulative ?? 0;
  const askTotal = asks.at(-1)?.cumulative ?? 0;
  const b = tick.buy_qty ?? bidTotal;
  const a = tick.sell_qty ?? askTotal;
  const best = bids[0];
  const offer = asks[0];
  return {
    bids,
    asks,
    bidTotal,
    askTotal,
    bookBid: tick.buy_qty,
    bookAsk: tick.sell_qty,
    imbalance: a + b > 0 ? b / (a + b) : null,
    spread: best && offer ? { paise: offer.price - best.price, ticks: Math.round((offer.price - best.price) / tickPaise) } : null,
  };
}

// --- time & sales --------------------------------------------------------------------------------

export const TAPE_MAX = 300;

/** New prints joined to the tape (newest first), never twice, at most TAPE_MAX kept. */
export function joinTape(tape: Print[], fresh: Print[]): Print[] {
  const newest = tape[0]?.seq ?? 0;
  const add = fresh.filter((p) => p.seq > newest).sort((a, b) => b.seq - a.seq);
  return add.length ? [...add, ...tape].slice(0, TAPE_MAX) : tape;
}

/** Prints at least `minQty` (0: all), and the buy/sell totals of what is shown. */
export function filterTape(tape: Print[], minQty: number): { rows: Print[]; bought: number; sold: number } {
  const rows = minQty > 0 ? tape.filter((p) => p.qty >= minQty) : tape;
  let bought = 0;
  let sold = 0;
  for (const p of rows) {
    if (p.side === "BUY") bought += p.qty;
    else if (p.side === "SELL") sold += p.qty;
  }
  return { rows, bought, sold };
}
