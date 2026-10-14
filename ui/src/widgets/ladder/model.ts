// The ladder's rules, kept apart from React so each one can be tested on its own.
//
//   tick (5-level depth, LTP, volume) ─┐
//   my working orders (store)        ├─► buildRows(centre, count) ─► one row per price on the tick grid
//   queue estimates (QueueTracker)   │      Work | PIQ | Bids | Price | Asks | LTQ | VAP
//   volume at price (VapCounter)    ─┘
//
// Every price here is whole paise; rows are always multiples of the instrument's tick size.

import type { Instrument, OrderView, Side, Tick } from "../../api/types";

// --- the price grid ------------------------------------------------------------------------------

/** The nearest price on the tick grid (25,010.07 with a 0.10 tick -> 25,010.10). */
export function snap(pricePaise: number, tickPaise: number): number {
  return Math.round(pricePaise / tickPaise) * tickPaise;
}

/** `count` prices, highest first, with `centre` (snapped) in the middle row. */
export function rowPrices(centre: number, count: number, tickPaise: number): number[] {
  const middle = snap(centre, tickPaise);
  const above = Math.floor((count - 1) / 2);
  const out: number[] = [];
  for (let i = 0; i < count; i++) out.push(middle + (above - i) * tickPaise);
  return out;
}

/**
 * Should auto-centre move the ladder? Only when the last price has left the middle half of
 * the rows: re-centring on every tick would make the rows jump under the mouse.
 */
export function needsRecentre(centre: number, ltp: number, count: number, tickPaise: number): boolean {
  const rowsAway = Math.abs(snap(ltp, tickPaise) - snap(centre, tickPaise)) / tickPaise;
  return rowsAway > Math.max(1, Math.floor(count / 4));
}

// --- my working orders ---------------------------------------------------------------------------

const WORKING = new Set<OrderView["status"]>(["PENDING", "OPEN", "PARTIAL"]);

export function leaves(o: OrderView): number {
  return Math.max(0, o.request.qty - o.filled_qty);
}

/** Orders still in the market (or on their way), on one instrument and account. */
export function workingOrders(orders: Record<string, OrderView>, instrumentId: string): OrderView[] {
  return Object.values(orders).filter(
    (o) => o.request.instrument_id === instrumentId && WORKING.has(o.status) && leaves(o) > 0,
  );
}

/** Orders whose outcome we don't know yet: nothing more is sent on that contract until they're answered. */
export function unconfirmed(orders: Record<string, OrderView>, instrumentId: string): OrderView[] {
  return Object.values(orders).filter(
    (o) => o.request.instrument_id === instrumentId && o.pending === "UNCONFIRMED",
  );
}

/** Orders shown at a price: limits at their price; MARKET/SL-M have none, so they show nowhere. */
export function priceOf(o: OrderView): number | null {
  return o.request.price_paise;
}

// --- queue position (PIQ) ------------------------------------------------------------------------

/**
 * Quantity ahead of a new order at `price`, from the visible depth (the paper broker uses the
 * same rule, so paper fills and the ladder's PIQ agree):
 * - our price is a depth level: everyone shown there is ahead of us
 * - we improve on the best price, or fall in an empty gap inside the depth: nobody is ahead
 * - beyond the 5 levels we can see: unknown (null)
 */
export function queueAt(side: Side, price: number, tick: Tick | null): number | null {
  if (!tick) return null;
  const levels = (side === "BUY" ? tick.bids : tick.asks).filter((l) => l[1] > 0);
  const level = levels.find((l) => l[0] === price);
  if (level) return level[1];
  if (!levels.length) return 0;
  const best = levels[0]![0];
  const worst = levels[levels.length - 1]![0];
  const improves = side === "BUY" ? price > best : price < best;
  const inside = side === "BUY" ? price > worst : price < worst;
  return improves || inside ? 0 : null;
}

function depthAt(side: Side, price: number, tick: Tick): number | null {
  const level = (side === "BUY" ? tick.bids : tick.asks).find((l) => l[0] === price && l[1] > 0);
  return level ? level[1] : null;
}

interface QueueEntry {
  price: number;
  ahead: number | null;
  seen: boolean; // seen working at least once (a click's order may not be in the store yet)
  waits: number; // updates since joining without being seen
}

/** A joined order not seen working after this many updates was refused or never placed. */
const UNSEEN_LIMIT = 30;

/**
 * Estimates how much quantity is ahead of each of my resting orders (Kite doesn't say).
 *
 *   joined(id, …) at click time  ─► ahead = depth at my price before my order shows in it
 *   a ladder opened later        ─► ahead = depth − my own quantity (assume the back of the queue)
 *   every tick                   ─► trades at my price eat the queue; the queue can't be
 *                                   longer than what's shown at my price minus me (cancels ahead)
 *   price changed (moved)        ─► back of the queue at the new price
 */
export class QueueTracker {
  private entries = new Map<string, QueueEntry>();
  private lastVolume: number | null = null;

  joined(clientOrderId: string, side: Side, price: number, tick: Tick | null): void {
    this.entries.set(clientOrderId, { price, ahead: queueAt(side, price, tick), seen: false, waits: 0 });
  }

  ahead(clientOrderId: string): number | null {
    return this.entries.get(clientOrderId)?.ahead ?? null;
  }

  /** Bring every estimate up to date with a new tick and the current working orders. */
  update(tick: Tick, working: OrderView[]): void {
    const traded =
      tick.volume != null && this.lastVolume != null && tick.volume > this.lastVolume
        ? tick.volume - this.lastVolume
        : 0;
    if (tick.volume != null) this.lastVolume = tick.volume;

    const alive = new Set<string>();
    for (const o of working) {
      const price = priceOf(o);
      if (price === null) continue;
      const id = o.request.client_order_id;
      alive.add(id);
      const mine = leaves(o);
      const entry = this.entries.get(id);
      if (!entry || entry.price !== price) {
        const shown = queueAt(o.request.side, price, tick);
        this.entries.set(id, {
          price,
          ahead: shown === null ? null : Math.max(0, shown - mine),
          seen: true,
          waits: 0,
        });
        continue;
      }
      entry.seen = true;
      if (entry.ahead === null) continue;
      let ahead = entry.ahead;
      if (traded && tick.ltp_paise === price) ahead = Math.max(0, ahead - traded);
      const shown = depthAt(o.request.side, price, tick);
      if (shown !== null) ahead = Math.min(ahead, Math.max(0, shown - mine));
      entry.ahead = ahead;
    }
    for (const [id, entry] of this.entries) {
      if (alive.has(id)) continue;
      entry.waits += 1;
      if (entry.seen || entry.waits > UNSEEN_LIMIT) this.entries.delete(id); // filled, cancelled or refused
    }
  }

  /** How many orders are tracked (for tests: nothing leaks). */
  get size(): number {
    return this.entries.size;
  }
}

// --- volume at price (VAP) -----------------------------------------------------------------------

/**
 * Today's volume at each price: the server's count when the ladder opens (GET …/vap), then
 * continued from the stream. Only volume after the server's count is added, so nothing is
 * counted twice. Volume going backwards means a new trading day.
 */
export class VapCounter {
  private levels = new Map<number, number>();
  private volume: number | null = null;

  reset(volume: number | null, levels: [number, number][]): void {
    this.volume = volume;
    this.levels = new Map(levels);
  }

  onTick(tick: Tick): void {
    if (tick.volume == null) return;
    if (this.volume === null) {
      this.volume = tick.volume; // no count from the server yet: start counting from here
      return;
    }
    if (tick.volume < this.volume) {
      this.volume = tick.volume; // a new trading day
      this.levels.clear();
      return;
    }
    const traded = tick.volume - this.volume;
    if (traded > 0) {
      this.levels.set(tick.ltp_paise, (this.levels.get(tick.ltp_paise) ?? 0) + traded);
      this.volume = tick.volume;
    }
  }

  at(price: number): number {
    return this.levels.get(price) ?? 0;
  }

  max(): number {
    let best = 0;
    for (const q of this.levels.values()) best = Math.max(best, q);
    return best;
  }
}

// --- the rows ------------------------------------------------------------------------------------

export interface LadderRow {
  price: number;
  bid: number | null; // quantity in the depth, null when not a visible level
  ask: number | null;
  bestBid: boolean;
  bestAsk: boolean;
  last: boolean; // the last traded price
  workBuy: number; // my working buy quantity at this price
  workSell: number;
  pendingCancel: boolean; // some of my orders here are being cancelled
  piq: number | null; // smallest queue ahead of my orders here
  ltq: number | null; // last trade size, on the last price row
  vap: number;
  vapShare: number; // 0-100, bar length relative to the busiest price
}

export interface RowInputs {
  prices: number[];
  tick: Tick | null;
  working: OrderView[];
  queue: QueueTracker;
  vap: VapCounter;
}

export function buildRows({ prices, tick, working, queue, vap }: RowInputs): LadderRow[] {
  const bids = new Map((tick?.bids ?? []).filter((l) => l[1] > 0).map((l) => [l[0], l[1]] as const));
  const asks = new Map((tick?.asks ?? []).filter((l) => l[1] > 0).map((l) => [l[0], l[1]] as const));
  const bestBid = tick?.bids.find((l) => l[1] > 0)?.[0];
  const bestAsk = tick?.asks.find((l) => l[1] > 0)?.[0];
  const busiest = vap.max();

  const byPrice = new Map<number, OrderView[]>();
  for (const o of working) {
    const p = priceOf(o);
    if (p === null) continue;
    const list = byPrice.get(p);
    if (list) list.push(o);
    else byPrice.set(p, [o]);
  }

  return prices.map((price) => {
    const mine = byPrice.get(price) ?? [];
    let workBuy = 0;
    let workSell = 0;
    let piq: number | null = null;
    for (const o of mine) {
      if (o.request.side === "BUY") workBuy += leaves(o);
      else workSell += leaves(o);
      const ahead = queue.ahead(o.request.client_order_id);
      if (ahead !== null) piq = piq === null ? ahead : Math.min(piq, ahead);
    }
    const volume = vap.at(price);
    const last = tick?.ltp_paise === price;
    return {
      price,
      bid: bids.get(price) ?? null,
      ask: asks.get(price) ?? null,
      bestBid: price === bestBid,
      bestAsk: price === bestAsk,
      last,
      workBuy,
      workSell,
      pendingCancel: mine.some((o) => o.pending === "CANCEL"),
      piq,
      ltq: last ? (tick?.last_qty ?? null) : null,
      vap: volume,
      vapShare: busiest ? Math.round((volume / busiest) * 100) : 0,
    };
  });
}

// --- building an order ---------------------------------------------------------------------------

export type LadderType = "LMT" | "MKT";
export type Tif = "DAY" | "IOC";

export interface OrderBody {
  client_order_id: string;
  instrument_id: string;
  side: Side;
  qty: number;
  order_type: "LIMIT" | "MARKET";
  product: string;
  validity: Tif;
  price_paise: number | null;
  market_protection: number | null;
}

let idCounter = 0;

/**
 * A fresh client order id (letters and digits, at most 20): made here, not by the server, so
 * that if the answer to "place" is lost the order can still be found and is never placed twice.
 */
export function newClientOrderId(now: number = Date.now(), random: () => number = Math.random): string {
  idCounter = (idCounter + 1) % 1296;
  const time = now.toString(36).toUpperCase(); // 8-9 chars until the year 2059
  const count = idCounter.toString(36).toUpperCase().padStart(2, "0");
  const noise = Math.floor(random() * 36 ** 4)
    .toString(36)
    .toUpperCase()
    .padStart(4, "0");
  return `L${time}${count}${noise}`;
}

export function buildOrder(args: {
  instrument: Instrument;
  side: Side;
  lots: number;
  price: number;
  type: LadderType;
  tif: Tif;
  product: string;
  clientOrderId?: string;
}): OrderBody {
  const market = args.type === "MKT";
  return {
    client_order_id: args.clientOrderId ?? newClientOrderId(),
    instrument_id: args.instrument.id,
    side: args.side,
    qty: args.lots * args.instrument.lot_size,
    order_type: market ? "MARKET" : "LIMIT",
    product: args.product,
    validity: args.tif,
    price_paise: market ? null : snap(args.price, args.instrument.tick_paise),
    // Kite refuses market orders without protection (0 is not allowed): -1 = the exchange's automatic band.
    market_protection: market ? -1 : null,
  };
}

/** Products that make sense for the instrument: delivery (CNC) for shares, NRML for F&O; MIS for both. */
export function productsFor(instrument: Pick<Instrument, "kind">): string[] {
  return instrument.kind === "EQ" ? ["CNC", "MIS"] : ["NRML", "MIS"];
}

/** Value of an order in paise (for the "confirm over" rule): qty × price, or × last price for MKT. */
export function orderValue(body: OrderBody, ltp: number | null): number {
  return body.qty * (body.price_paise ?? ltp ?? 0);
}
