// Rules shared by the order book, positions and audit trail, kept apart from React so each
// can be tested on its own. Every state is written out in words; colour only repeats it
// (docs/design/ui.md §1).

import type { AuditEvent, ChargeBill, OrderView, Position, PositionsSnapshot } from "../../api/types";
import { positionKey } from "../../state/store";
import { clock, price, qty, rupees, shortName } from "../../lib/format";

export type Tone = "buy" | "good" | "bad" | "pending" | "dim" | "";

// --- orders ----------------------------------------------------------------------------------

/** The order's state as a trader says it. Pending flags win: they're what happens next. */
export function statusText(o: OrderView): string {
  if (o.pending === "UNCONFIRMED") return "Unconfirmed";
  if (o.pending === "CANCEL") return "Pending cancel";
  if (o.pending === "MODIFY") return "Pending change";
  switch (o.status) {
    case "PENDING":
      return "Pending new";
    case "OPEN":
      return "Working";
    case "PARTIAL":
      return "Partial";
    case "FILLED":
      return "Filled";
    case "CANCELLED":
      return o.filled_qty > 0 ? "Cancelled (part filled)" : "Cancelled";
    case "REJECTED":
      return "Rejected";
  }
}

export function statusTone(o: OrderView): Tone {
  if (o.pending !== "NONE") return "pending";
  switch (o.status) {
    case "FILLED":
      return "good";
    case "REJECTED":
      return "bad";
    case "CANCELLED":
      return "dim";
    default:
      return "buy";
  }
}

export type OrderTab = "working" | "filled" | "rejected" | "all";

/** Working: still in the market or on its way. Filled: anything that traded. Rejected: refused. */
export function inTab(o: OrderView, tab: OrderTab): boolean {
  switch (tab) {
    case "working":
      return o.status === "PENDING" || o.status === "OPEN" || o.status === "PARTIAL";
    case "filled":
      return o.filled_qty > 0;
    case "rejected":
      return o.status === "REJECTED";
    case "all":
      return true;
  }
}

/** Orders for a tab, newest first. */
export function ordersFor(orders: Record<string, OrderView>, tab: OrderTab): OrderView[] {
  return Object.values(orders)
    .filter((o) => inTab(o, tab))
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at) || (a.request.client_order_id < b.request.client_order_id ? 1 : -1));
}

export function tabCounts(orders: Record<string, OrderView>): Record<OrderTab, number> {
  const counts: Record<OrderTab, number> = { working: 0, filled: 0, rejected: 0, all: 0 };
  for (const o of Object.values(orders)) for (const tab of Object.keys(counts) as OrderTab[]) if (inTab(o, tab)) counts[tab] += 1;
  return counts;
}

/** Can the trader still cancel it? (working, and not already being cancelled or checked) */
export function cancellable(o: OrderView): boolean {
  return inTab(o, "working") && o.pending === "NONE";
}

/** The price column: the limit, "MKT", or "SL 25,000.00 → 24,990.00" for stops. */
export function orderPrice(o: OrderView): string {
  const r = o.request;
  if (r.order_type === "MARKET") return "MKT";
  if (r.order_type === "SL-M") return `SL-M ${r.trigger_paise !== null ? price(r.trigger_paise) : ""}`.trim();
  if (r.order_type === "SL") return `${r.trigger_paise !== null ? price(r.trigger_paise) : ""} → ${r.price_paise !== null ? price(r.price_paise) : ""}`;
  return r.price_paise !== null ? price(r.price_paise) : "";
}

// --- positions -------------------------------------------------------------------------------

export interface PositionTotals {
  realised: number;
  unrealised: number;
  pnl: number; // gross
  charges: number; // the day's charges
  net: number; // gross − charges
}

/** Positions to show: open ones first (largest exposure first), then closed ones with P&L today. */
export function positionRows(positions: Record<string, Position>): Position[] {
  return Object.values(positions)
    .filter((p) => p.qty !== 0 || p.realised_paise !== 0 || p.buy_qty !== 0 || p.sell_qty !== 0)
    .sort((a, b) => {
      if ((a.qty === 0) !== (b.qty === 0)) return a.qty === 0 ? 1 : -1;
      return Math.abs(b.qty * (b.ltp_paise ?? 0)) - Math.abs(a.qty * (a.ltp_paise ?? 0)) || a.instrument_id.localeCompare(b.instrument_id);
    });
}

export function positionTotals(rows: Position[], charges: ChargeBill | null = null): PositionTotals {
  const totals = rows.reduce(
    (t, p) => ({ ...t, realised: t.realised + p.realised_paise, unrealised: t.unrealised + p.unrealised_paise, pnl: t.pnl + p.pnl_paise }),
    { realised: 0, unrealised: 0, pnl: 0, charges: 0, net: 0 },
  );
  // the day's charges (from the server), including positions closed and hidden from the rows
  totals.charges = charges?.total_paise ?? 0;
  totals.net = totals.pnl - totals.charges;
  return totals;
}

/** A position's charges today (the server's figure for this instrument and product). */
export function chargesFor(p: Position, charges: ChargeBill | null): number {
  return charges?.by_position[positionKey(p)] ?? 0;
}

/** "100.08" for an exact average of 10007.5 paise: the server sends averages as decimal text. */
export function averagePrice(avgPaiseText: string): string {
  const paise = Number(avgPaiseText);
  if (!Number.isFinite(paise) || paise === 0) return "–";
  return price(Math.round(paise));
}

/** The positions title bar: is Laddrix's view the same as the broker's? */
export function reconcileState(
  r: PositionsSnapshot["reconcile"] | null,
  alerts: number,
  now: number,
): { text: string; tone: Tone; title: string } {
  if (alerts > 0) {
    return { text: `✗ ${alerts} differ${alerts === 1 ? "s" : ""} from broker`, tone: "bad", title: "Check the broker before trading those contracts" };
  }
  if (!r || !r.checked_at) return { text: "not checked yet", tone: "dim", title: "Positions haven't been compared with the broker yet" };
  const mins = Math.max(0, Math.floor((now - Date.parse(r.checked_at)) / 60_000));
  const ago = mins === 0 ? "now" : `${mins}m`;
  if (r.error) return { text: `? broker ${ago}`, tone: "pending", title: `Couldn't check the broker: ${r.error}` };
  return { text: `✓ = broker ${ago}`, tone: "good", title: `Positions matched the broker at ${clock(r.checked_at, false)}` };
}

// --- the audit trail -------------------------------------------------------------------------

/** What happened, in the words of an exchange's execution report. */
export function execType(e: AuditEvent): { text: string; tone: Tone } {
  const o = e.order;
  switch (e.kind) {
    case "SUBMITTED":
      return { text: "Pending new", tone: "dim" };
    case "SENT":
      return { text: "New", tone: "buy" };
    case "REJECTED":
      return { text: "Rejected", tone: "bad" };
    case "UNCONFIRMED":
      return { text: "Unconfirmed", tone: "pending" };
    case "FILL":
      // the trade itself; the order's new state ("Filled", "Partial") follows as UPDATED
      return o && o.filled_qty >= o.request.qty ? { text: "Fill", tone: "good" } : { text: "Partial fill", tone: "good" };
    case "MODIFY_SENT":
      return { text: "Pending replace", tone: "pending" };
    case "MODIFY_UNCONFIRMED":
      return { text: "Replace unconfirmed", tone: "pending" };
    case "MODIFY_SENDING": // recorded on disk just before a change is sent (#265)
      return { text: "Replace sending", tone: "pending" };
    case "CANCEL_SENT":
      return { text: "Pending cancel", tone: "pending" };
    case "CANCELLED_LOCALLY":
      return { text: "Cancelled", tone: "dim" };
    case "IGNORED":
      return { text: "Ignored", tone: "dim" };
    case "EXTERNAL":
      return { text: "External", tone: "pending" };
    case "KILL_SWITCH":
      return e.extra.engaged ? { text: "STOP", tone: "bad" } : { text: "Resume", tone: "good" };
    case "UPDATED":
      if (!o) return { text: "Updated", tone: "" };
      switch (o.status) {
        case "CANCELLED":
          return { text: "Cancelled", tone: "dim" };
        case "REJECTED":
          return { text: "Rejected", tone: "bad" };
        case "FILLED":
          return { text: "Filled", tone: "good" };
        case "PARTIAL":
          return { text: "Partial", tone: "good" };
        default:
          return { text: o.pending === "NONE" ? "Working" : "Replaced", tone: "buy" };
      }
    default:
      if (e.kind.startsWith("POSITION_MISMATCH")) return { text: "Position check", tone: "pending" };
      return { text: e.kind, tone: "" };
  }
}

/** The detail column: the fill, the reason, the broker's id, or what changed. */
export function auditDetail(e: AuditEvent): string {
  if (e.kind === "KILL_SWITCH") {
    const scope = e.account_id === "firm" ? "whole firm" : e.account_id;
    return `${scope}: ${e.detail}${e.extra.by ? ` (by ${String(e.extra.by)})` : ""}`;
  }
  if (e.fill) return `${qty(e.fill.qty)} @ ${price(e.fill.price_paise)}${e.fill.exact ? "" : " (average)"}`;
  if (e.kind === "SENT" && e.order?.broker_order_id) return `broker id ${e.order.broker_order_id}`;
  if (e.kind === "SUBMITTED" && e.order) {
    const r = e.order.request;
    return `${qty(r.qty)} @ ${r.price_paise !== null ? price(r.price_paise) : r.order_type} ${r.product} ${r.validity}`;
  }
  return e.detail || e.order?.reason || "";
}

export type AuditFilter = "all" | "fills" | "rejects" | "cancels" | "stops";

export function matchesFilter(e: AuditEvent, filter: AuditFilter, contract: string): boolean {
  if (contract) {
    const id = e.order?.request.instrument_id ?? "";
    if (!id.toUpperCase().includes(contract.trim().toUpperCase())) return false;
  }
  switch (filter) {
    case "all":
      return true;
    case "fills":
      return e.kind === "FILL";
    case "rejects":
      return e.kind === "REJECTED" || (e.kind === "UPDATED" && e.order?.status === "REJECTED");
    case "cancels":
      return e.kind === "CANCEL_SENT" || e.kind === "CANCELLED_LOCALLY" || (e.kind === "UPDATED" && e.order?.status === "CANCELLED");
    case "stops":
      return e.kind === "KILL_SWITCH";
  }
}

/** Merge pages (newest first) without duplicates: an event is identified by its log position. */
export function mergeEvents(current: AuditEvent[], incoming: AuditEvent[]): AuditEvent[] {
  const seen = new Set(current.map((e) => e.seq));
  const added = incoming.filter((e) => !seen.has(e.seq));
  if (!added.length) return current;
  return [...current, ...added].sort((a, b) => b.seq - a.seq);
}

export const CSV_HEADER = ["Transact time", "Account", "Contract", "Order", "User", "Exec type", "Side", "Qty", "Filled", "Price", "Detail"];

export function auditRow(e: AuditEvent): string[] {
  const r = e.order?.request;
  return [
    e.ts,
    e.account_id,
    r ? shortName(r.instrument_id) : "",
    e.client_order_id,
    r?.user_id ?? String(e.extra.by ?? ""),
    execType(e).text,
    r?.side ?? "",
    r ? String(r.qty) : "",
    e.order ? String(e.order.filled_qty) : "",
    r?.price_paise != null ? price(r.price_paise) : r ? r.order_type : "",
    auditDetail(e),
  ];
}

/** CSV with every field quoted, so commas in prices (25,010.00) and reasons stay in their column. */
export function toCsv(rows: string[][]): string {
  const cell = (s: string) => {
    const safe = /^[=+\-@\t\r]/.test(s) ? `'${s}` : s; // never let a spreadsheet run a "formula"
    return `"${safe.replace(/"/g, '""')}"`;
  };
  return rows.map((r) => r.map(cell).join(",")).join("\r\n") + "\r\n";
}

/** Day P&L, signed, for totals rows. */
export const signedRupees = (paise: number) => rupees(paise, { sign: true });
