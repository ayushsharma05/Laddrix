// The order ticket's rules (docs/design/ui.md §4 "Order ticket"), apart from React so each
// can be tested on its own. The server checks everything again; these are here so the
// trader sees the problem before the click, in words, with the field to fix.

import type { Instrument, Side } from "../../api/types";
import { newClientOrderId, snap, type OrderBody, type Tif } from "../ladder/model";

export type TicketType = "LMT" | "MKT" | "SL" | "SL-M";

export const TICKET_TYPES: TicketType[] = ["LMT", "MKT", "SL", "SL-M"];
export const needsPrice = (t: TicketType) => t === "LMT" || t === "SL";
export const needsTrigger = (t: TicketType) => t === "SL" || t === "SL-M";
const ORDER_TYPE: Record<TicketType, OrderBody["order_type"]> = { LMT: "LIMIT", MKT: "MARKET", SL: "SL", "SL-M": "SL-M" };

/** What "-1 (automatic) market protection" means in paper trading (broker/rules.py). */
export const AUTO_PROTECTION_PCT = 2;

/** "25,010.1" or "25010.10" -> 2501010 paise; null for anything that isn't a price. */
export function parsePrice(text: string): number | null {
  const clean = text.replace(/[,\s₹]/g, "");
  const m = /^(\d+)(?:\.(\d{1,2}))?$/.exec(clean);
  if (!m) return null;
  const paise = Number(m[1]) * 100 + Number((m[2] ?? "").padEnd(2, "0"));
  return paise > 0 ? paise : null;
}

/** The price `ticks` steps away on the grid (never below one tick). */
export function stepPrice(price: number, tickPaise: number, ticks: number): number {
  return Math.max(tickPaise, snap(price, tickPaise) + ticks * tickPaise);
}

export interface TicketInput {
  instrument: Instrument;
  side: Side;
  lots: number;
  type: TicketType;
  price: number | null;
  trigger: number | null;
  tif: Tif;
  product: string;
}

/** Why this ticket can't be sent as it stands, or null. `ltp` checks stops the market already passed. */
export function ticketProblem(t: TicketInput, ltp: number | null): string | null {
  if (!t.instrument.tradable) return `${t.instrument.symbol} can't be traded`;
  if (!(t.lots > 0)) return "quantity is 0: set the lots";
  if (needsPrice(t.type)) {
    if (t.price === null) return "enter a price";
    if (t.price % t.instrument.tick_paise !== 0) return `price must be a multiple of the tick (${(t.instrument.tick_paise / 100).toFixed(2)})`;
  }
  if (needsTrigger(t.type)) {
    if (t.trigger === null) return "enter a trigger price";
    if (t.trigger % t.instrument.tick_paise !== 0) return `trigger must be a multiple of the tick (${(t.instrument.tick_paise / 100).toFixed(2)})`;
    if (t.type === "SL" && t.price !== null) {
      if (t.side === "BUY" && t.trigger > t.price) return "stop-loss buy: the trigger must not be above the limit price";
      if (t.side === "SELL" && t.trigger < t.price) return "stop-loss sell: the trigger must not be below the limit price";
    }
    if (ltp !== null) {
      if (t.side === "BUY" && t.trigger <= ltp) return "a buy stop's trigger must be above the last price (it would fire at once)";
      if (t.side === "SELL" && t.trigger >= ltp) return "a sell stop's trigger must be below the last price (it would fire at once)";
    }
  }
  if ((t.type === "SL" || t.type === "SL-M") && t.tif === "IOC") return "stop orders wait for their trigger: use DAY, not IOC";
  return null;
}

export function buildTicketOrder(t: TicketInput, clientOrderId: string = newClientOrderId()): OrderBody {
  const market = t.type === "MKT" || t.type === "SL-M";
  return {
    client_order_id: clientOrderId,
    instrument_id: t.instrument.id,
    side: t.side,
    qty: t.lots * t.instrument.lot_size,
    order_type: ORDER_TYPE[t.type],
    product: t.product,
    validity: t.tif,
    price_paise: needsPrice(t.type) ? t.price : null,
    trigger_paise: needsTrigger(t.type) ? t.trigger : null,
    // Kite refuses market orders without protection (0 is not allowed): -1 = automatic band
    market_protection: market ? -1 : null,
  };
}

/** The worst price a market order may trade at with automatic protection, on the grid. */
export function paysAtMost(side: Side, reference: number, tickPaise: number): number {
  const raw = (reference * (100 + (side === "BUY" ? AUTO_PROTECTION_PCT : -AUTO_PROTECTION_PCT))) / 100;
  const ticks = side === "BUY" ? Math.floor(raw / tickPaise) : Math.ceil(raw / tickPaise);
  return Math.max(ticks * tickPaise, tickPaise);
}

export interface Summary {
  value: number | null; // paise: what the order is worth at its price (or the protected worst for market)
  worstPosition: number; // position if this and every working order on the same side fill
  distancePct: number | null; // the order's price vs the last price, + = above
  worstPrice: number | null; // market orders: the protected limit
}

/**
 * The line under the ticket: value, worst-case position and distance from the last price.
 * `position` = net position now; `workingSameSide` = what's still working on this side.
 */
export function summarise(t: TicketInput, ltp: number | null, position: number, workingSameSide: number): Summary {
  const qty = t.lots * t.instrument.lot_size;
  const market = t.type === "MKT" || t.type === "SL-M";
  const reference = t.type === "SL-M" ? t.trigger : ltp;
  const worstPrice = market && reference !== null ? paysAtMost(t.side, reference, t.instrument.tick_paise) : null;
  const priced = market ? worstPrice : t.price;
  const signed = t.side === "BUY" ? qty + workingSameSide : -(qty + workingSameSide);
  return {
    value: priced !== null ? priced * qty : null,
    worstPosition: position + signed,
    distancePct: priced !== null && ltp ? ((priced - ltp) / ltp) * 100 : null,
    worstPrice,
  };
}
