import { act, fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactElement } from "react";
import type { AuditEvent, OrderView, Position } from "../../api/types";
import { AppProvider, createApp } from "../../app/context";
import { fakeRest, fakeStream } from "../../test/fakes";
import { ME, order } from "../../test/fixtures";
import { AuditTrail, CATCH_UP_MS, type AuditParams } from "./AuditTrail";
import { Messages } from "./Messages";
import { OrderBook } from "./OrderBook";
import { Positions } from "./Positions";

const T = "2026-10-14T04:41:00+00:00";
const SNAP = { positions: [], realised_paise: 0, unrealised_paise: 0, day_pnl_paise: 0, reconcile: { checked_at: null, ok: null, error: "", alerts: [] } };

// eslint-disable-next-line @typescript-eslint/no-explicit-any -- each widget takes its own params
function setup(widget: (props: any) => ReactElement, params: Record<string, unknown>, routes: Record<string, unknown> = {}, open = true) {
  const server = fakeRest(routes);
  const { stream, socket } = fakeStream();
  const app = createApp({ rest: server.rest, stream });
  app.store.setMe(ME);
  stream.start();
  if (open) socket().open();
  const props = { params: { link: "pink", ...params }, setParams: vi.fn(), setInstrument: vi.fn(), publish: vi.fn(), open: vi.fn() };
  const view = render(<AppProvider app={app}>{widget(props)}</AppProvider>);
  const rerender = (change: Record<string, unknown>) => {
    props.params = { ...props.params, ...change };
    view.rerender(<AppProvider app={app}>{widget(props)}</AppProvider>);
  };
  return { ...server, ...props, app, socket, rerender };
}

const rows = (label: string) => within(screen.getByRole("grid", { name: label })).queryAllByRole("row");
const changes = (calls: { method: string; path: string }[]) => calls.filter((c) => c.method !== "GET");

describe("Order book", () => {
  const book: OrderView[] = [
    order("w1", "OPEN", T, { created_at: "2026-10-14T04:40:00+00:00" }),
    order("w2", "OPEN", T, { pending: "CANCEL", created_at: "2026-10-14T04:40:30+00:00" }),
    order("p1", "PARTIAL", T, { filled_qty: 25, avg_price_paise: 2501000, created_at: "2026-10-14T04:41:00+00:00" }),
    order("f1", "FILLED", T, { filled_qty: 75, avg_price_paise: 2500990, created_at: "2026-10-14T04:30:00+00:00" }),
    order("r1", "REJECTED", T, { reason: "over max order value" }),
  ];

  function withOrders(tab: string, routes: Record<string, unknown> = {}, open = true) {
    const s = setup((p) => <OrderBook {...p} />, { tab }, routes, open);
    act(() => s.app.store.applyOrders("acc-a", book));
    return s;
  }

  it("the status tabs show the right orders, newest first, with counts", () => {
    const { rerender } = withOrders("working");
    expect(screen.getByRole("button", { name: "Working 3" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Filled 2" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Rejected 1" })).toBeTruthy();
    expect(rows("Orders").map((r) => r.textContent)).toEqual([
      expect.stringContaining("Partial"),
      expect.stringContaining("Pending cancel"),
      expect.stringContaining("Working"),
    ]);
    rerender({ tab: "rejected" });
    expect(rows("Orders")[0]!.textContent).toContain("Rejected: over max order value");
    rerender({ tab: "filled" });
    expect(rows("Orders").map((r) => r.textContent)).toEqual([expect.stringContaining("25,010.00"), expect.stringContaining("25,009.90")]);
  });

  it("clicking a tab saves it in the widget's settings", () => {
    const { setParams } = withOrders("working");
    fireEvent.click(screen.getByRole("button", { name: "Filled 2" }));
    expect(setParams).toHaveBeenCalledWith({ tab: "filled" });
  });

  it("× cancels one order; Cancel all cancels only orders that can be cancelled", async () => {
    const routes = Object.fromEntries(
      ["w1", "p1"].map((id) => [`DELETE /api/accounts/acc-a/orders/${id}`, order(id, "OPEN", "2026-10-14T04:42:00+00:00", { pending: "CANCEL" })]),
    );
    const { calls } = withOrders("working", routes);
    const w1 = rows("Orders").find((r) => r.title.startsWith("w1"))!;
    fireEvent.click(within(w1).getByRole("button", { name: "Cancel BUY 75 NIFTY26OCTFUT" }));
    await vi.waitFor(() => expect(changes(calls)).toHaveLength(1));
    // w1 is now pending cancel: only p1 can still be cancelled
    await vi.waitFor(() => expect(screen.getAllByRole("button", { name: /^Cancel BUY/ })).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: /Cancel all/ }));
    await vi.waitFor(() => expect(changes(calls)).toHaveLength(2));
    // w1 was already being cancelled, so Cancel all sent only p1
    expect(changes(calls).map((c) => c.path)).toEqual(["/api/accounts/acc-a/orders/w1", "/api/accounts/acc-a/orders/p1"]);
    await vi.waitFor(() => expect(screen.queryAllByRole("button", { name: /^Cancel BUY/ })).toHaveLength(0));
  });

  it("a closing order is marked as a close; a plain order is not", () => {
    const base = order("c1", "OPEN", T);
    const close = { ...base, request: { ...base.request, side: "SELL" as const, reduce_only: true } };
    const s = setup((p) => <OrderBook {...p} />, { tab: "working" });
    act(() => s.app.store.applyOrders("acc-a", [close, order("p1", "OPEN", T)]));
    const [c1, p1] = ["c1", "p1"].map((id) => rows("Orders").find((r) => r.title.startsWith(id))!);
    expect(within(c1!).getByTitle(/can't reverse the position/).textContent).toBe(" close");
    expect(within(p1!).queryByTitle(/can't reverse the position/)).toBeNull();
  });

  it("an unanswered order is marked never placed in two clicks, by the server", async () => {
    const unanswered = order("u1", "PENDING", T, { pending: "UNCONFIRMED", broker_order_id: null });
    const settled = order("u1", "REJECTED", T, { reason: "settled by ayush: not in the broker's own order book, so never placed" });
    const s = setup((p) => <OrderBook {...p} />, { tab: "working" }, { "POST /api/accounts/acc-a/orders/u1/settle": settled });
    act(() => s.app.store.applyOrders("acc-a", [unanswered, order("w1", "OPEN", T)]));
    expect(screen.getAllByRole("button", { name: "Not placed?" })).toHaveLength(1); // only the unanswered one
    fireEvent.click(screen.getByRole("button", { name: "Not placed?" }));
    expect(changes(s.calls)).toHaveLength(0); // the first click only asks
    fireEvent.click(screen.getByRole("button", { name: "Confirm: not in the broker's book" }));
    await vi.waitFor(() => expect(changes(s.calls)).toEqual([{ method: "POST", path: "/api/accounts/acc-a/orders/u1/settle", body: { confirm: true } }]));
    await vi.waitFor(() => expect(screen.queryByRole("button", { name: /Not placed|Confirm: not/ })).toBeNull());
  });

  it("Cancel all still works while trading is stopped: getting out must always work", async () => {
    const routes = { "DELETE /api/accounts/acc-a/orders/w1": order("w1", "CANCELLED", "2026-10-14T04:42:00+00:00"), "DELETE /api/accounts/acc-a/orders/p1": order("p1", "CANCELLED", "2026-10-14T04:42:00+00:00") };
    const { calls, socket } = withOrders("working", routes);
    act(() => socket().receive({ type: "kill", scope: "account acc-a", engaged: true, reason: "break", by: "ayush", at: null }));
    fireEvent.click(screen.getByRole("button", { name: /Cancel all/ }));
    await vi.waitFor(() => expect(changes(calls)).toHaveLength(2));
  });

  it("while disconnected, cancels aren't sent and the reason goes to Messages", () => {
    const { calls, app } = withOrders("working", {}, false);
    fireEvent.click(screen.getByRole("button", { name: /Cancel all/ }));
    expect(changes(calls)).toHaveLength(0);
    expect(app.store.getState().messages[0]!.text).toContain("Not cancelled: disconnected");
  });

  it("click sends the contract to the link group; double-click opens the order's audit trail", () => {
    const { publish, open } = withOrders("working");
    fireEvent.click(rows("Orders")[0]!);
    expect(publish).toHaveBeenCalledWith("NFO:NIFTY26OCTFUT");
    fireEvent.doubleClick(rows("Orders")[0]!);
    expect(open).toHaveBeenCalledWith("audit", { link: "none", orderId: "p1" });
  });
});

describe("Positions", () => {
  const p = (id: string, qty: number, extra: Partial<Position> = {}): Position => ({
    instrument_id: id, product: "NRML", qty, avg_price_paise: "2492530", ltp_paise: 2501010, realised_paise: 0,
    unrealised_paise: 0, pnl_paise: 0, buy_qty: Math.max(qty, 0), sell_qty: 0, buy_value_paise: 0, sell_value_paise: 0, updated_at: T, ...extra,
  });

  it("shows each position, the total and that they match the broker", () => {
    const { app, socket } = setup((x) => <Positions {...x} />, {});
    act(() =>
      app.store.applyPositions("acc-a", {
        ...SNAP,
        day_pnl_paise: 1481250,
        positions: [
          p("NFO:NIFTY26OCTFUT", 150, { unrealised_paise: 1272000, pnl_paise: 1272000 }),
          p("NSE:RELIANCE", 10, { avg_price_paise: "139980", ltp_paise: 140020, unrealised_paise: 400, pnl_paise: 400, product: "CNC" }),
          p("NFO:BANKNIFTY26OCTFUT", 0, { realised_paise: 208850, pnl_paise: 208850, buy_qty: 35, sell_qty: 35 }),
        ],
        reconcile: { checked_at: new Date(Date.now() - 150_000).toISOString(), ok: true, error: "", alerts: [] },
      }),
    );
    const [first, second, closed, total] = rows("Positions");
    expect(first!.textContent).toContain("NIFTY26OCTFUT");
    expect(first!.textContent).toContain("+150");
    expect(first!.textContent).toContain("24,925.30");
    expect(second!.textContent).toContain("RELIANCE");
    expect(closed!.textContent).toContain("+₹2,088.50");
    expect(total!.textContent).toContain("+₹14,812.50");
    expect(screen.getByText("✓ = broker 2m")).toBeTruthy();
    expect(screen.getAllByText("+₹14,812.50")).toHaveLength(4); // no charges yet: gross = net, bar and total
    // a fill's charges arrive from the server: net = gross − charges, per row and for the day
    act(() =>
      socket().receive({
        type: "charges",
        account_id: "acc-a",
        total_paise: 11152,
        turnover_paise: 375750000,
        lines: [{ label: "STT", paise: 9412 }, { label: "Brokerage", paise: 1740 }],
        by_position: { "NFO:NIFTY26OCTFUT|NRML": 11152 },
        schedule: { name: "Kite standard plan", checked_on: "2026-10-15", source: "" },
      }),
    );
    const [nifty, , , sum] = rows("Positions");
    expect(nifty!.textContent).toContain("111.52");
    expect(nifty!.textContent).toContain("+₹12,608.48"); // 12,720.00 − 111.52
    expect(sum!.textContent).toContain("+₹14,700.98");
    expect(screen.getByText("+₹14,700.98", { selector: ".book-bar b" })).toBeTruthy();
  });

  it("a position that differs from the broker is marked on its row and in the bar", () => {
    const { app, socket } = setup((x) => <Positions {...x} />, {});
    act(() => app.store.applyPositions("acc-a", { ...SNAP, positions: [p("NFO:NIFTY26OCTFUT", 75), { ...p("NFO:NIFTY26OCTFUT", -75), product: "MIS" }] }));
    act(() =>
      socket().receive({ type: "position_alert", account_id: "acc-a", state: "raised", instrument_id: "NFO:NIFTY26OCTFUT", product: "NRML", ours: 75, broker: 150, message: "MockTrade shows +75, the broker shows +150" }),
    );
    const nrml = rows("Positions").find((r) => r.textContent!.includes("NRML"))!;
    const mis = rows("Positions").find((r) => r.textContent!.includes("MIS"))!;
    expect(nrml.textContent).toContain("broker 150");
    expect(nrml.className).toContain("differs");
    expect(mis.className).not.toContain("differs"); // only the product that differs is marked
    expect(screen.getByText("✗ 1 differs from broker")).toBeTruthy();
  });
});

describe("Messages", () => {
  it("lists newest first, filters to problems and clears", () => {
    const { app, rerender, setParams } = setup((x) => <Messages {...x} />, { level: "all" });
    act(() => {
      app.store.addMessage("INFO", "", "first");
      app.store.addMessage("ERROR", "NFO:NIFTY26OCTFUT", "BUY 75 NIFTY26OCTFUT refused: limit");
      app.store.addMessage("OK", "", "third");
    });
    expect(rows("Messages").map((r) => r.textContent)).toEqual([expect.stringContaining("third"), expect.stringContaining("refused"), expect.stringContaining("first")]);
    fireEvent.click(screen.getByRole("button", { name: "Problems 1" }));
    expect(setParams).toHaveBeenCalledWith({ level: "problems" });
    rerender({ level: "problems" });
    expect(rows("Messages")).toHaveLength(1);
    expect(rows("Messages")[0]!.textContent).toContain("NIFTY26OCTFUT");
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(app.store.getState().messages).toHaveLength(0);
  });
});

describe("Audit trail", () => {
  const e = (seq: number, kind: string, id = "o1", extra: Partial<AuditEvent> = {}): AuditEvent => ({
    seq, ts: `2026-10-14T04:41:${String(seq).padStart(2, "0")}.000+00:00`, account_id: "acc-a", kind, client_order_id: id, detail: "",
    order: order(id, "OPEN", T), fill: null, extra: {}, ...extra,
  });
  const PAGE1 = [
    e(5, "FILL", "o1", { order: order("o1", "FILLED", T, { filled_qty: 75 }), fill: { client_order_id: "o1", qty: 75, price_paise: 2501000, exact: true } }),
    e(4, "SENT", "o2"),
    e(3, "REJECTED", "o3", { detail: "over max order value" }),
    e(2, "SENT", "o1", { order: order("o1", "OPEN", T, { broker_order_id: "B1" }) }),
    e(1, "SUBMITTED", "o1"),
  ];

  function audit(params: Partial<AuditParams> = {}, routes: Record<string, unknown> = {}) {
    return setup((x) => <AuditTrail {...x} />, { filter: "all", contract: "", live: true, ...params }, {
      "GET /api/accounts/acc-a/events": PAGE1,
      ...routes,
    });
  }

  it("shows the account's events newest first, in execution-report words", async () => {
    audit();
    await vi.waitFor(() => expect(rows("Audit trail")).toHaveLength(5));
    expect(rows("Audit trail").map((r) => r.querySelector("[class^=tone-]")!.textContent)).toEqual(["Fill", "New", "Rejected", "New", "Pending new"]);
    expect(rows("Audit trail")[0]!.textContent).toContain("75 @ 25,010.00");
    expect(rows("Audit trail")[3]!.textContent).toContain("broker id B1");
  });

  it("another account starts empty: never the previous account's events", async () => {
    const { app } = audit({ filter: "rejects" });
    await vi.waitFor(() => expect(rows("Audit trail")).toHaveLength(1)); // acc-a's refused order
    act(() => app.store.selectAccount("acc-view")); // its own trail isn't in yet (refused here)
    await act(async () => {});
    expect(screen.queryByText(/over max order value/)).toBeNull();
  });

  it("filters by type and contract", async () => {
    const { rerender } = audit({ filter: "rejects" });
    await vi.waitFor(() => expect(rows("Audit trail")).toHaveLength(1));
    expect(rows("Audit trail")[0]!.textContent).toContain("over max order value");
    rerender({ filter: "all", contract: "SBIN" });
    expect(screen.getByText("Nothing matches the filter.")).toBeTruthy();
  });

  it("live: catches up from the log after an order event on the stream", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { socket, calls } = audit({}, { "GET /api/accounts/acc-a/events?after_seq=5&limit=1000": [e(7, "CANCEL_SENT", "o2"), e(6, "MODIFY_SENT", "o2")] });
      await vi.waitFor(() => expect(rows("Audit trail")).toHaveLength(5));
      act(() => socket().receive({ type: "order", account_id: "acc-a", kind: "CANCEL_SENT", detail: "", order: order("o2", "OPEN", T), fill: null }));
      await act(() => vi.advanceTimersByTimeAsync(CATCH_UP_MS[0]! + 10));
      await vi.waitFor(() => expect(rows("Audit trail")).toHaveLength(7));
      expect(rows("Audit trail")[0]!.textContent).toContain("Pending cancel");
      expect(calls.some((c) => c.path.includes("after_seq=5"))).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it("live: ignores order events from other accounts", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { socket, calls } = audit();
      await vi.waitFor(() => expect(rows("Audit trail")).toHaveLength(5));
      act(() => socket().receive({ type: "order", account_id: "acc-view", kind: "SENT", detail: "", order: order("x", "OPEN", T), fill: null }));
      await act(() => vi.advanceTimersByTimeAsync(CATCH_UP_MS[1]! + 10));
      expect(calls.filter((c) => c.path.includes("after_seq"))).toHaveLength(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it("paused: stream events don't change the list", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { socket, calls } = audit({ live: false });
      await vi.waitFor(() => expect(rows("Audit trail")).toHaveLength(5));
      act(() => socket().receive({ type: "order", account_id: "acc-a", kind: "SENT", detail: "", order: order("o9", "OPEN", T), fill: null }));
      await act(() => vi.advanceTimersByTimeAsync(10_000));
      expect(calls.filter((c) => c.path.includes("after_seq"))).toHaveLength(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it("Older loads the page before the oldest row shown", async () => {
    const full = Array.from({ length: 200 }, (_, i) => e(400 - i, "SENT", `o${i}`));
    const { calls } = audit({}, { "GET /api/accounts/acc-a/events": full, "GET /api/accounts/acc-a/events?before_seq=201&limit=200": [e(150, "SUBMITTED", "old")] });
    await vi.waitFor(() => expect(rows("Audit trail")).toHaveLength(200));
    fireEvent.click(screen.getByRole("button", { name: "Older…" }));
    await vi.waitFor(() => expect(rows("Audit trail")).toHaveLength(201));
    expect(calls.some((c) => c.path.endsWith("before_seq=201&limit=200"))).toBe(true);
    expect(screen.queryByRole("button", { name: "Older…" })).toBeNull(); // a short page: nothing older
  });

  it("clicking a row opens that order's whole timeline, oldest first", async () => {
    const history = [PAGE1[4]!, PAGE1[3]!, PAGE1[0]!];
    const { setParams, rerender, publish } = audit({}, { "GET /api/accounts/acc-a/orders/o1/history": history });
    await vi.waitFor(() => expect(rows("Audit trail")).toHaveLength(5));
    fireEvent.click(rows("Audit trail")[0]!);
    expect(setParams).toHaveBeenCalledWith({ orderId: "o1" });
    expect(publish).toHaveBeenCalledWith("NFO:NIFTY26OCTFUT");
    rerender({ orderId: "o1" });
    const timeline = await screen.findByRole("complementary", { name: "Timeline of o1" });
    await vi.waitFor(() => expect(within(timeline).getAllByRole("listitem")).toHaveLength(3));
    expect(within(timeline).getAllByRole("listitem").map((li) => li.querySelector("b")!.textContent)).toEqual(["Pending new", "New", "Fill"]);
  });
});
