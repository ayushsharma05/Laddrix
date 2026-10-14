import type { AuditEvent, OrderView, Position } from "../../api/types";
import { order } from "../../test/fixtures";
import {
  auditDetail,
  auditRow,
  averagePrice,
  cancellable,
  execType,
  matchesFilter,
  mergeEvents,
  orderPrice,
  ordersFor,
  positionRows,
  positionTotals,
  reconcileState,
  statusText,
  tabCounts,
  toCsv,
} from "./model";

const T = "2026-10-14T04:41:00+00:00";

function ev(seq: number, kind: string, extra: Partial<AuditEvent> = {}): AuditEvent {
  return { seq, ts: T, account_id: "acc-a", kind, client_order_id: "o1", detail: "", order: order("o1", "OPEN", T), fill: null, extra: {}, ...extra };
}

function pos(id: string, qty: number, extra: Partial<Position> = {}): Position {
  return {
    instrument_id: id, product: "NRML", qty, avg_price_paise: "2501000", ltp_paise: 2501100,
    realised_paise: 0, unrealised_paise: 0, pnl_paise: 0, buy_qty: Math.max(qty, 0), sell_qty: Math.max(-qty, 0),
    buy_value_paise: 0, sell_value_paise: 0, updated_at: T, ...extra,
  };
}

describe("order states in words", () => {
  it("names every state, pending flags first", () => {
    expect(statusText(order("a", "PENDING", T))).toBe("Pending new");
    expect(statusText(order("a", "OPEN", T))).toBe("Working");
    expect(statusText(order("a", "PARTIAL", T, { filled_qty: 25 }))).toBe("Partial");
    expect(statusText(order("a", "OPEN", T, { pending: "CANCEL" }))).toBe("Pending cancel");
    expect(statusText(order("a", "PENDING", T, { pending: "UNCONFIRMED" }))).toBe("Unconfirmed");
    expect(statusText(order("a", "FILLED", T, { filled_qty: 75 }))).toBe("Filled");
    expect(statusText(order("a", "CANCELLED", T))).toBe("Cancelled");
    expect(statusText(order("a", "CANCELLED", T, { filled_qty: 25 }))).toBe("Cancelled (part filled)");
    expect(statusText(order("a", "REJECTED", T))).toBe("Rejected");
  });

  it("only working orders with nothing pending can be cancelled", () => {
    expect(cancellable(order("a", "OPEN", T))).toBe(true);
    expect(cancellable(order("a", "PARTIAL", T))).toBe(true);
    expect(cancellable(order("a", "OPEN", T, { pending: "CANCEL" }))).toBe(false);
    expect(cancellable(order("a", "PENDING", T, { pending: "UNCONFIRMED" }))).toBe(false);
    expect(cancellable(order("a", "FILLED", T))).toBe(false);
  });

  it("shows limit, market and stop prices", () => {
    const o = order("a", "OPEN", T);
    expect(orderPrice(o)).toBe("25,010.00");
    expect(orderPrice({ ...o, request: { ...o.request, order_type: "MARKET", price_paise: null } })).toBe("MKT");
    expect(orderPrice({ ...o, request: { ...o.request, order_type: "SL", trigger_paise: 2500000, price_paise: 2499000 } })).toBe("25,000.00 → 24,990.00");
    expect(orderPrice({ ...o, request: { ...o.request, order_type: "SL-M", trigger_paise: 2500000, price_paise: null } })).toBe("SL-M 25,000.00");
  });
});

describe("order book tabs", () => {
  const orders: Record<string, OrderView> = {
    w: order("w", "OPEN", T, { created_at: "2026-10-14T04:40:00+00:00" }),
    p: order("p", "PARTIAL", T, { filled_qty: 25, created_at: "2026-10-14T04:42:00+00:00" }),
    f: order("f", "FILLED", T, { filled_qty: 75, created_at: "2026-10-14T04:30:00+00:00" }),
    c: order("c", "CANCELLED", T),
    r: order("r", "REJECTED", T),
  };

  it("sorts each tab newest first", () => {
    expect(ordersFor(orders, "working").map((o) => o.request.client_order_id)).toEqual(["p", "w"]);
    expect(ordersFor(orders, "filled").map((o) => o.request.client_order_id)).toEqual(["p", "f"]);
    expect(ordersFor(orders, "rejected").map((o) => o.request.client_order_id)).toEqual(["r"]);
    expect(ordersFor(orders, "all")).toHaveLength(5);
  });

  it("an order on its way to the broker (pending new) is working", () => {
    expect(ordersFor({ n: order("n", "PENDING", T) }, "working")).toHaveLength(1);
  });

  it("counts each tab", () => {
    expect(tabCounts(orders)).toEqual({ working: 2, filled: 2, rejected: 1, all: 5 });
  });
});

describe("positions", () => {
  it("open positions first, biggest exposure first, then closed ones with P&L; flat untouched ones hidden", () => {
    const rows = positionRows({
      a: pos("NSE:SBIN", 10, { ltp_paise: 87300 }),
      b: pos("NFO:NIFTY26OCTFUT", -75),
      c: pos("NSE:INFY", 0, { realised_paise: 5000, buy_qty: 10, sell_qty: 10 }),
      d: pos("NSE:TCS", 0, { buy_qty: 0, sell_qty: 0 }),
    });
    expect(rows.map((p) => p.instrument_id)).toEqual(["NFO:NIFTY26OCTFUT", "NSE:SBIN", "NSE:INFY"]);
  });

  it("adds up realised, unrealised and total P&L", () => {
    const t = positionTotals([pos("A", 1, { realised_paise: 100, unrealised_paise: -40, pnl_paise: 60 }), pos("B", 1, { realised_paise: 0, unrealised_paise: 15, pnl_paise: 15 })]);
    expect(t).toEqual({ realised: 100, unrealised: -25, pnl: 75 });
  });

  it("shows exact averages rounded to the paisa", () => {
    expect(averagePrice("10007.5")).toBe("100.08");
    expect(averagePrice("2501000")).toBe("25,010.00");
    expect(averagePrice("0")).toBe("–");
  });

  it("says whether positions match the broker", () => {
    const now = Date.parse("2026-10-14T04:45:00+00:00");
    expect(reconcileState(null, 0, now).text).toBe("not checked yet");
    expect(reconcileState({ checked_at: "2026-10-14T04:43:00+00:00", ok: true, error: "", alerts: [] }, 0, now)).toMatchObject({ text: "✓ = broker 2m", tone: "good" });
    expect(reconcileState({ checked_at: "2026-10-14T04:44:50+00:00", ok: true, error: "", alerts: [] }, 0, now).text).toBe("✓ = broker now");
    expect(reconcileState({ checked_at: "2026-10-14T04:43:00+00:00", ok: null, error: "timeout", alerts: [] }, 0, now)).toMatchObject({ tone: "pending" });
    expect(reconcileState({ checked_at: "2026-10-14T04:43:00+00:00", ok: false, error: "", alerts: [] }, 1, now)).toMatchObject({ text: "✗ 1 differs from broker", tone: "bad" });
  });
});

describe("the audit trail", () => {
  it("uses execution-report words", () => {
    const full = order("o1", "FILLED", T, { filled_qty: 75 });
    const part = order("o1", "PARTIAL", T, { filled_qty: 25 });
    expect(execType(ev(1, "SUBMITTED")).text).toBe("Pending new");
    expect(execType(ev(2, "SENT")).text).toBe("New");
    expect(execType(ev(3, "FILL", { order: part })).text).toBe("Partial fill");
    expect(execType(ev(4, "FILL", { order: full })).text).toBe("Fill");
    expect(execType(ev(4, "UPDATED", { order: full })).text).toBe("Filled"); // the state change after it
    expect(execType(ev(5, "CANCEL_SENT")).text).toBe("Pending cancel");
    expect(execType(ev(6, "UPDATED", { order: order("o1", "CANCELLED", T) })).text).toBe("Cancelled");
    expect(execType(ev(7, "IGNORED")).text).toBe("Ignored");
    expect(execType(ev(8, "KILL_SWITCH", { order: null, extra: { engaged: true, by: "ayush" } }))).toEqual({ text: "STOP", tone: "bad" });
    expect(execType(ev(9, "KILL_SWITCH", { order: null, extra: { engaged: false } })).text).toBe("Resume");
    expect(execType(ev(10, "POSITION_MISMATCH_RAISED", { order: null })).text).toBe("Position check");
  });

  it("details: fills, broker ids, what was asked, reasons, who stopped", () => {
    expect(auditDetail(ev(1, "FILL", { fill: { client_order_id: "o1", qty: 25, price_paise: 2501000, exact: true } }))).toBe("25 @ 25,010.00");
    expect(auditDetail(ev(1, "FILL", { fill: { client_order_id: "o1", qty: 25, price_paise: 2501000, exact: false } }))).toBe("25 @ 25,010.00 (average)");
    expect(auditDetail(ev(2, "SENT", { order: order("o1", "OPEN", T, { broker_order_id: "251014000123" }) }))).toBe("broker id 251014000123");
    expect(auditDetail(ev(3, "SUBMITTED"))).toBe("75 @ 25,010.00 NRML DAY");
    expect(auditDetail(ev(4, "REJECTED", { detail: "over max order value" }))).toBe("over max order value");
    expect(auditDetail(ev(5, "KILL_SWITCH", { account_id: "firm", order: null, detail: "exchange issue", extra: { by: "admin" } }))).toBe("whole firm: exchange issue (by admin)");
  });

  it("filters by type and contract", () => {
    const fill = ev(1, "FILL");
    const reject = ev(2, "REJECTED");
    const stop = ev(3, "KILL_SWITCH", { order: null });
    expect(matchesFilter(fill, "fills", "")).toBe(true);
    expect(matchesFilter(reject, "fills", "")).toBe(false);
    expect(matchesFilter(reject, "rejects", "")).toBe(true);
    expect(matchesFilter(stop, "stops", "")).toBe(true);
    expect(matchesFilter(fill, "all", "nifty")).toBe(true);
    expect(matchesFilter(fill, "all", "SBIN")).toBe(false);
    expect(matchesFilter(stop, "all", "nifty")).toBe(false);
  });

  it("merges pages newest first without duplicates", () => {
    const merged = mergeEvents([ev(5, "SENT"), ev(4, "SUBMITTED")], [ev(6, "FILL"), ev(5, "SENT"), ev(2, "SUBMITTED")]);
    expect(merged.map((e) => e.seq)).toEqual([6, 5, 4, 2]);
    const same = [ev(1, "SENT")];
    expect(mergeEvents(same, [ev(1, "SENT")])).toBe(same); // nothing new: no re-render
  });

  it("exports CSV that keeps commas in their column and can't run spreadsheet formulas", () => {
    const row = auditRow(ev(1, "REJECTED", { detail: '=HYPERLINK("x"), then "quotes"' }));
    expect(row.slice(2, 7)).toEqual(["NIFTY26OCTFUT", "o1", "ayush", "Rejected", "BUY"]);
    const csv = toCsv([["a,b", row[10]!]]);
    expect(csv).toBe('"a,b","\'=HYPERLINK(""x""), then ""quotes"""\r\n');
  });
});
