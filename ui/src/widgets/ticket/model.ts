// The order ticket's rules (docs/design/ui.md §4 "Order ticket"), apart from React so each
// can be tested on its own. Nothing here knows which order types exist: they come from the
// server's order spec (domain/order_spec.py), so a broker that adds or drops a type changes
// the ticket without a new release. The server checks everything again; these are here so
// the trader sees a problem before the click, in words, with the field to fix.

import type { OrderSpec, OrderTypeSpec, Side } from "../../api/types";
import { newClientOrderId, snap, type OrderBody } from "../ladder/model";

export const needsPrice = (t: OrderTypeSpec) => t.price === "required";
export const needsTrigger = (t: OrderTypeSpec) => t.trigger === "required";
/** Trades without a price of its own (market, stop-market): priced by protection instead. */
export const isMarketLike = (t: OrderTypeSpec) => t.price === "none" && t.market_protection === "auto";

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

/** The order type the ticket shows: the saved one if the spec still offers it, else the first. */
export function pickType(spec: OrderSpec, saved: string | undefined): OrderTypeSpec | null {
  return spec.order_types.find((t) => t.code === saved || t.label === saved) ?? spec.order_types[0] ?? null;
}

/** A validity the type allows: the saved one if allowed, else the type's first. */
export function pickValidity(type: OrderTypeSpec, saved: string | undefined): string {
  return saved && type.validities.includes(saved) ? saved : (type.validities[0] ?? "DAY");
}

export interface TicketInput {
  spec: OrderSpec;
  type: OrderTypeSpec;
  symbol: string;
  side: Side;
  lots: number;
  price: number | null;
  trigger: number | null;
  validity: string;
  product: string;
}

const tickText = (tick: number) => (tick / 100).toFixed(2);

/** Why this ticket can't be sent as it stands, or null. `ltp` checks stops the market already passed. */
export function ticketProblem(t: TicketInput, ltp: number | null): string | null {
  const { spec, type } = t;
  if (!spec.tradable) return `${t.symbol} can't be traded`;
  if (!(t.lots > 0)) return "quantity is 0: set the lots";
  if (needsPrice(type)) {
    if (t.price === null) return "enter a price";
    if (t.price % spec.tick_paise !== 0) return `price must be a multiple of the tick (${tickText(spec.tick_paise)})`;
  }
  if (needsTrigger(type)) {
    if (t.trigger === null) return "enter a trigger price";
    if (t.trigger % spec.tick_paise !== 0) return `trigger must be a multiple of the tick (${tickText(spec.tick_paise)})`;
  }
  if (type.rules.includes("stop_limit") && t.price !== null && t.trigger !== null) {
    if (t.side === "BUY" && t.trigger > t.price) return "stop-loss buy: the trigger must not be above the limit price";
    if (t.side === "SELL" && t.trigger < t.price) return "stop-loss sell: the trigger must not be below the limit price";
  }
  if (type.rules.includes("stop_trigger") && t.trigger !== null && ltp !== null) {
    if (t.side === "BUY" && t.trigger <= ltp) return "a buy stop's trigger must be above the last price (it would fire at once)";
    if (t.side === "SELL" && t.trigger >= ltp) return "a sell stop's trigger must be below the last price (it would fire at once)";
  }
  if (!type.validities.includes(t.validity)) return `${type.name} orders can't be ${t.validity} (allowed: ${type.validities.join(", ")})`;
  if (!spec.products.includes(t.product)) return `product ${t.product} isn't offered here (allowed: ${spec.products.join(", ")})`;
  return null;
}

export function buildTicketOrder(t: TicketInput, instrumentId: string, clientOrderId: string = newClientOrderId()): OrderBody {
  return {
    client_order_id: clientOrderId,
    instrument_id: instrumentId,
    side: t.side,
    qty: t.lots * t.spec.lot_size,
    order_type: t.type.code,
    product: t.product,
    validity: t.validity,
    price_paise: needsPrice(t.type) ? t.price : null,
    trigger_paise: needsTrigger(t.type) ? t.trigger : null,
    // -1 = the exchange's automatic protection band (Kite refuses 0)
    market_protection: t.type.market_protection === "auto" ? -1 : null,
  };
}

/** The worst price a protected market order may trade at, on the grid. */
export function paysAtMost(side: Side, reference: number, tickPaise: number, protectionPct: number): number {
  const raw = (reference * (100 + (side === "BUY" ? protectionPct : -protectionPct))) / 100;
  const ticks = side === "BUY" ? Math.floor(raw / tickPaise) : Math.ceil(raw / tickPaise);
  return Math.max(ticks * tickPaise, tickPaise);
}

export interface Summary {
  value: number | null; // paise: what the order is worth at its price (or the protected worst for market)
  worstPosition: number; // position if this and every working order on the same side fill
  distancePct: number | null; // the order's price vs the last price, + = above
  worstPrice: number | null; // market-like orders: the protected limit
}

/**
 * The line under the ticket: value, worst-case position and distance from the last price.
 * `position` = net position now; `workingSameSide` = what's still working on this side.
 */
export function summarise(t: TicketInput, ltp: number | null, position: number, workingSameSide: number): Summary {
  const qty = t.lots * t.spec.lot_size;
  const market = isMarketLike(t.type);
  const reference = needsTrigger(t.type) ? t.trigger : ltp;
  const worstPrice =
    market && reference !== null ? paysAtMost(t.side, reference, t.spec.tick_paise, Number(t.spec.auto_protection_pct)) : null;
  const priced = market ? worstPrice : needsPrice(t.type) ? t.price : ltp;
  const signed = t.side === "BUY" ? qty + workingSameSide : -(qty + workingSameSide);
  return {
    value: priced !== null ? priced * qty : null,
    worstPosition: position + signed,
    distancePct: priced !== null && ltp ? ((priced - ltp) / ltp) * 100 : null,
    worstPrice,
  };
}
