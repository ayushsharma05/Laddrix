import type { Position, PositionsSnapshot } from "../api/types";
import { ME, order } from "../test/fixtures";
import { cancelBlock, tradingBlock } from "./gate";
import { alertFor, describeOrderEvent, MAX_MESSAGES, Store } from "./store";

function position(qty: number, updated: string): Position {
  return {
    instrument_id: "NFO:NIFTY26OCTFUT",
    product: "NRML",
    qty,
    avg_price_paise: "2501000",
    ltp_paise: 2501100,
    realised_paise: 0,
    unrealised_paise: 7500,
    pnl_paise: 7500,
    buy_qty: qty,
    sell_qty: 0,
    buy_value_paise: 0,
    sell_value_paise: 0,
    updated_at: updated,
  };
}

function snapshot(positions: Position[], dayPnl = 0): PositionsSnapshot {
  return {
    positions,
    realised_paise: 0,
    unrealised_paise: 0,
    day_pnl_paise: dayPnl,
    reconcile: { checked_at: null, ok: null, error: "", alerts: [] },
  };
}

describe("Store", () => {
  it("picks the first tradable account and keeps the choice across reloads", () => {
    const store = new Store();
    store.setMe(ME);
    expect(store.getState().accountId).toBe("acc-a");
    store.selectAccount("acc-view");
    store.setMe(ME);
    expect(store.getState().accountId).toBe("acc-view");
    store.selectAccount("acc-nobody");
    expect(store.getState().accountId).toBe("acc-view");
  });

  it("keeps the newest order when a snapshot arrives after a stream event", () => {
    const store = new Store();
    store.setMe(ME);
    store.applyMessage({
      type: "order",
      account_id: "acc-a",
      kind: "UPDATED",
      detail: "",
      order: order("o1", "FILLED", "2026-10-14T04:45:02+00:00"),
      fill: null,
    });
    // the snapshot was read just before the fill
    store.applyOrders("acc-a", [order("o1", "OPEN", "2026-10-14T04:45:01+00:00"), order("o2", "OPEN", "2026-10-14T04:45:00+00:00")]);
    const orders = store.getState().books["acc-a"]!.orders;
    expect(orders.o1!.status).toBe("FILLED");
    expect(orders.o2!.status).toBe("OPEN");
  });

  it("compares times, not text (different time zones)", () => {
    const store = new Store();
    store.applyOrders("acc-a", [order("o1", "FILLED", "2026-10-14T04:45:02+00:00")]);
    // 10:15:01 India time is 04:45:01 UTC: older, though its text sorts later
    store.applyOrders("acc-a", [order("o1", "OPEN", "2026-10-14T10:15:01+05:30")]);
    expect(store.getState().books["acc-a"]!.orders.o1!.status).toBe("FILLED");
  });

  it("merges positions the same way and takes the day P&L", () => {
    const store = new Store();
    store.applyMessage({ type: "positions", account_id: "acc-a", positions: [position(150, "2026-10-14T04:45:05+00:00")], day_pnl_paise: 900 });
    store.applyPositions("acc-a", snapshot([position(75, "2026-10-14T04:45:00+00:00")], 500));
    const book = store.getState().books["acc-a"]!;
    expect(book.positions["NFO:NIFTY26OCTFUT|NRML"]!.qty).toBe(150);
    expect(book.dayPnl).toBe(500);
  });

  it("tracks kill switches from /api/me and from the stream", () => {
    const store = new Store();
    store.setMe({ ...ME, firm_stopped: true, accounts: [{ ...ME.accounts[1]!, stopped: true, stop_reason: "break" }] });
    expect(store.getState().firmKill.engaged).toBe(true);
    expect(store.getState().books["acc-a"]!.kill).toEqual({ engaged: true, reason: "break", by: "" });

    store.applyMessage({ type: "kill", scope: "account acc-a", engaged: false, reason: "", by: "risk", at: null });
    expect(store.getState().books["acc-a"]!.kill.engaged).toBe(false);
    store.applyMessage({ type: "kill", scope: "firm", engaged: false, reason: "", by: "admin", at: null });
    expect(store.getState().firmKill.engaged).toBe(false);
    expect(store.getState().messages[0]!.text).toBe("Trading resumed for the whole firm by admin");
    expect(store.getState().messages[1]!.text).toBe("Trading resumed for account acc-a by risk");
  });

  it("raises and clears position alerts", () => {
    const store = new Store();
    const alert = {
      type: "position_alert" as const,
      account_id: "acc-a",
      state: "raised" as const,
      instrument_id: "NFO:NIFTY26OCTFUT",
      product: "MIS" as string | null,
      ours: 75,
      broker: 150,
      message: "differs",
    };
    store.applyMessage(alert);
    store.applyMessage({ ...alert, product: "NRML", broker: 0 }); // another product: kept apart
    store.applyMessage({ ...alert, instrument_id: "NSE:SBIN", product: null, broker: 9 }); // the net
    const alerts = store.getState().books["acc-a"]!.alerts;
    expect(Object.keys(alerts).sort()).toEqual(["NFO:NIFTY26OCTFUT|MIS", "NFO:NIFTY26OCTFUT|NRML", "NSE:SBIN|"]);
    expect(alertFor(alerts, { instrument_id: "NFO:NIFTY26OCTFUT", product: "MIS" })!.broker).toBe(150);
    expect(alertFor(alerts, { instrument_id: "NFO:NIFTY26OCTFUT", product: "CNC" })).toBeUndefined();
    expect(alertFor(alerts, { instrument_id: "NSE:SBIN", product: "CNC" })!.broker).toBe(9); // net: every row
    expect(store.getState().messages[0]!.level).toBe("WARN");
    store.applyMessage({ ...alert, state: "cleared" });
    expect(Object.keys(store.getState().books["acc-a"]!.alerts).sort()).toEqual(["NFO:NIFTY26OCTFUT|NRML", "NSE:SBIN|"]);
    expect(store.getState().messages[0]!.text).toBe("NIFTY26OCTFUT MIS: positions match the broker again");
  });

  it("keeps at most MAX_MESSAGES, newest first", () => {
    const store = new Store();
    for (let i = 0; i < MAX_MESSAGES + 5; i += 1) store.addMessage("INFO", "", `m${i}`);
    const messages = store.getState().messages;
    expect(messages).toHaveLength(MAX_MESSAGES);
    expect(messages[0]!.text).toBe(`m${MAX_MESSAGES + 4}`);
  });

  it("counts orders sent in the last minute", () => {
    let now = 1_000_000;
    const store = new Store(() => now);
    store.noteSent();
    now += 30_000;
    store.noteSent();
    expect(store.sentLastMinute()).toBe(2);
    now += 31_000;
    expect(store.sentLastMinute()).toBe(1);
  });

  it("only notifies listeners on real changes to the stream status", () => {
    const store = new Store();
    const listener = vi.fn();
    store.subscribe(listener);
    store.setStream("open");
    store.setStream("open");
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("forgets everything on logout", () => {
    const store = new Store();
    store.setMe(ME);
    store.applyOrders("acc-a", [order("o1", "OPEN", "2026-10-14T04:45:00+00:00")]);
    store.setMe(null);
    expect(store.getState().books).toEqual({});
    expect(store.getState().accountId).toBeNull();
  });
});

describe("Messages lines for order events", () => {
  it("writes fills, refusals and unknown outcomes as sentences", () => {
    const o = order("o1", "PARTIAL", "t");
    expect(describeOrderEvent("FILL", "", o, { qty: 25, price_paise: 2501000, exact: true })).toEqual({
      level: "OK",
      text: "Bought 25 NIFTY26OCTFUT @ 25,010.00",
    });
    expect(describeOrderEvent("FILL", "", o, { qty: 25, price_paise: 2501000, exact: false })!.text).toMatch(/\(average\)$/);
    expect(describeOrderEvent("REJECTED", "over the loss limit", o, null)).toEqual({
      level: "ERROR",
      text: "BUY 75 NIFTY26OCTFUT refused: over the loss limit",
    });
    expect(describeOrderEvent("UNCONFIRMED", "", o, null)!.level).toBe("WARN");
    expect(describeOrderEvent("UPDATED", "", { ...o, status: "CANCELLED" }, null)!.text).toBe("BUY 75 NIFTY26OCTFUT cancelled");
    expect(describeOrderEvent("UPDATED", "", { ...o, status: "REJECTED", reason: "margin" }, null)!.text).toMatch(/rejected by the broker: margin/);
    expect(describeOrderEvent("SENT", "", o, null)).toBeNull();
    expect(describeOrderEvent("UPDATED", "", o, null)).toBeNull();
  });
});

describe("tradingBlock", () => {
  const ready = () => {
    const store = new Store();
    store.setMe(ME);
    store.setStream("open");
    return store;
  };

  it("allows trading when connected on a tradable, running account", () => {
    expect(tradingBlock(ready().getState())).toBeNull();
  });

  it("explains each reason trading is blocked", () => {
    expect(tradingBlock(new Store().getState())).toBe("not logged in");
    const s = ready();
    s.setStream("reconnecting");
    expect(tradingBlock(s.getState())).toMatch(/disconnected/);
    s.setStream("open");
    s.selectAccount("acc-view");
    expect(tradingBlock(s.getState())).toMatch(/view acc-view but not trade/);
    s.selectAccount("acc-a");
    s.applyMessage({ type: "kill", scope: "account acc-a", engaged: true, reason: "break", by: "me", at: null });
    expect(tradingBlock(s.getState())).toBe("trading is stopped on acc-a: break");
    s.applyMessage({ type: "kill", scope: "firm", engaged: true, reason: "exchange issue", by: "risk", at: null });
    expect(tradingBlock(s.getState())).toBe("all trading is stopped: exchange issue");
  });

  it("blocks new orders until the broker's daily login, but not cancels", () => {
    const s = new Store();
    s.setMe({ ...ME, accounts: ME.accounts.map((a) => (a.id === "acc-a" ? { ...a, broker: "kite", broker_session: "login_needed" as const } : a)) });
    s.setStream("open");
    s.selectAccount("acc-a");
    expect(tradingBlock(s.getState())).toBe("log in to Kite first: today's login is missing or expired");
    expect(cancelBlock(s.getState())).toBeNull();
  });

  it("blocks an account that isn't running on the server", () => {
    const s = new Store();
    s.setMe({ ...ME, accounts: [{ ...ME.accounts[1]!, running: false }] });
    s.setStream("open");
    expect(tradingBlock(s.getState())).toMatch(/isn't running/);
  });
});
