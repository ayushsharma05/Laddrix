import { act, fireEvent, render, screen, within } from "@testing-library/react";
import type { OrderView } from "../../api/types";
import { AppProvider, createApp } from "../../app/context";
import { fakeRest, fakeStream, tick } from "../../test/fakes";
import { ME, order, orderSpec } from "../../test/fixtures";
import { Ladder, type LadderParams } from "./Ladder";

const ID = "NFO:NIFTY26OCTFUT";
const NIFTY = { id: ID, exchange: "NFO", symbol: "NIFTY26OCTFUT", kind: "FUT", tick_paise: 10, lot_size: 75, expiry: "2026-10-27", strike_paise: null, underlying: "NIFTY", tradable: true, ltp_paise: 2501000 };
const BOOK = tick(ID, 2501000, {
  close_paise: 2491480,
  volume: 2000,
  bids: [
    [2501000, 150, 2],
    [2500990, 300, 3],
  ],
  asks: [
    [2501010, 75, 1],
    [2501020, 225, 2],
  ],
});

/** What the server answers to a new order: the order, working. */
function accepted(body: Record<string, unknown>): OrderView {
  const o = order(String(body.client_order_id), "OPEN", "2026-10-14T04:41:00+00:00");
  return { ...o, request: { ...o.request, ...(body as Partial<OrderView["request"]>) } };
}

function setup(params: Partial<LadderParams> = {}, routes: Record<string, unknown> = {}) {
  const server = fakeRest({
    [`GET /api/instruments/${encodeURIComponent(ID)}`]: NIFTY,
    [`GET /api/instruments/${encodeURIComponent(ID)}/vap`]: { volume: 2000, levels: [[2501000, 600]] },
    "POST /api/accounts/acc-a/orders": (body: unknown) => ({ status: 201, body: accepted(body as Record<string, unknown>) }),
    [`GET /api/accounts/acc-a/instruments/${encodeURIComponent(ID)}/order-spec`]: orderSpec(ID),
    [`GET /api/accounts/acc-view/instruments/${encodeURIComponent(ID)}/order-spec`]: orderSpec(ID),
    ...routes,
  });
  const { stream, socket } = fakeStream();
  const app = createApp({ rest: server.rest, stream });
  app.store.setMe(ME); // acc-a can trade, acc-view can only watch
  stream.start();
  socket().open();
  const props = {
    params: { link: "pink", instrumentId: ID, lots: 2, type: "LMT", tif: "DAY", confirmOverPaise: 0, ...params } as LadderParams,
    setParams: vi.fn(),
    setInstrument: vi.fn(),
    publish: vi.fn(),
    open: vi.fn(),
  };
  const view = render(
    <AppProvider app={app}>
      <Ladder {...props} />
    </AppProvider>,
  );
  const push = (t = BOOK) => act(() => socket().receive({ type: "ticks", ticks: [t] }));
  const rerender = (change: Partial<LadderParams>) => {
    props.params = { ...props.params, ...change };
    view.rerender(
      <AppProvider app={app}>
        <Ladder {...props} />
      </AppProvider>,
    );
  };
  return { ...server, ...props, app, socket, push, rerender };
}

/** Prices are on screen and the order spec has arrived (the Type list is filled). */
async function ready() {
  await screen.findByText("25,010.00", { selector: ".price" });
  await vi.waitFor(() => expect((screen.getByLabelText("Type") as HTMLSelectElement).options.length).toBeGreaterThan(0));
}

const row = (p: string) => screen.getByText(p, { selector: ".price" }).closest("[role=row]") as HTMLElement;
const statusLine = () => document.querySelector(".ladder-status") as HTMLElement;
const posts = (calls: { method: string; path: string; body: unknown }[]) => calls.filter((c) => c.method !== "GET");

describe("Ladder", () => {
  it("lays out prices on the tick grid around the last price, with depth and VAP", async () => {
    const { push } = setup();
    await ready();
    push();
    const prices = [...document.querySelectorAll(".ladder-rows .price")].map((e) => e.textContent);
    expect(prices).toHaveLength(30);
    expect(prices).toContain("25,010.00");
    for (const p of prices) expect(Math.round(Number(p!.replace(/,/g, "")) * 100) % 10).toBe(0);
    const best = row("25,010.00");
    expect(within(best).getByLabelText("Buy at 25,010.00").textContent).toBe("150");
    expect(best.querySelector(".price.last")).not.toBeNull();
    expect(best.querySelector(".vap")!.textContent).toBe("600");
    expect(within(row("25,010.10")).getByLabelText("Sell at 25,010.10").textContent).toBe("75");
    expect(screen.getByText("+95.20")).toBeTruthy(); // net change from the previous close
  });

  it("click Bids buys the lots box at that price; click Asks sells", async () => {
    const { push, calls } = setup({ lots: 2 });
    await ready();
    push();
    fireEvent.click(screen.getByLabelText("Buy at 25,009.90"));
    fireEvent.click(screen.getByLabelText("Sell at 25,010.20"));
    await vi.waitFor(() => expect(posts(calls)).toHaveLength(2));
    const [buy, sell] = posts(calls);
    expect(buy!.path).toBe("/api/accounts/acc-a/orders");
    expect(buy!.body).toMatchObject({ instrument_id: ID, side: "BUY", qty: 150, order_type: "LIMIT", price_paise: 2500990, validity: "DAY", product: "NRML", market_protection: null });
    expect(sell!.body).toMatchObject({ side: "SELL", qty: 150, price_paise: 2501020 });
    expect((buy!.body as { client_order_id: string }).client_order_id).toMatch(/^[A-Za-z0-9]{1,20}$/);
    expect(statusLine().textContent).toContain("SELL 150 NIFTY26OCTFUT @ 25,010.20 DAY sent");
  });

  it("shows my working order on its row, and the queue ahead of it counts down", async () => {
    const { push, app } = setup({ lots: 1 });
    await ready();
    push();
    fireEvent.click(screen.getByLabelText("Buy at 25,009.90")); // 300 ahead
    await vi.waitFor(() => expect(row("25,009.90").querySelector(".work")!.textContent).toBe("75"));
    expect(row("25,009.90").querySelector(".piq")!.textContent).toBe("300");
    // 120 trade at 25,009.90; my 75 now shows in the depth
    push({ ...BOOK, ltp_paise: 2500990, volume: 2120, bids: [[2500990, 255, 3]] });
    expect(row("25,009.90").querySelector(".piq")!.textContent).toBe("180");
    expect(Object.keys(app.store.getState().books["acc-a"]!.orders)).toHaveLength(1);
  });

  it("MKT sends no price and always market protection; Ctrl+click joins the best price", async () => {
    const { push, calls, rerender } = setup({ lots: 1, type: "MKT" });
    await ready();
    push();
    fireEvent.click(screen.getByLabelText("Buy at 25,009.90"));
    rerender({ type: "LMT" });
    fireEvent.click(screen.getByLabelText("Sell at 25,010.50"), { ctrlKey: true });
    await vi.waitFor(() => expect(posts(calls)).toHaveLength(2));
    expect(posts(calls)[0]!.body).toMatchObject({ order_type: "MARKET", price_paise: null, market_protection: -1 });
    expect(posts(calls)[1]!.body).toMatchObject({ side: "SELL", price_paise: 2501010 }); // the best ask
  });

  it("refuses at once, and says why, when the lots box is 0", async () => {
    const { push, calls, app } = setup({ lots: 0 });
    await ready();
    push();
    fireEvent.click(screen.getByLabelText("Buy at 25,010.00"));
    expect(posts(calls)).toHaveLength(0);
    expect(statusLine().textContent).toContain("quantity is 0");
    expect(app.store.getState().messages[0]!.level).toBe("WARN"); // also in Messages
  });

  it("never sends while trading is stopped, and says so", async () => {
    const { push, calls, socket } = setup();
    await ready();
    push();
    act(() => socket().receive({ type: "kill", scope: "account acc-a", engaged: true, reason: "break", by: "ayush", at: null }));
    const buy = screen.getByLabelText("Buy at 25,010.00") as HTMLButtonElement;
    expect(buy.disabled).toBe(true);
    fireEvent.click(buy);
    expect(posts(calls)).toHaveLength(0);
    expect(statusLine().textContent).toContain("Trading paused: trading is stopped on acc-a: break");
  });

  it("a view-only account can't send from the ladder", async () => {
    const { push, calls } = setup({ accountId: "acc-view" });
    await ready();
    push();
    fireEvent.click(screen.getByLabelText("Buy at 25,010.00"));
    expect(posts(calls)).toHaveLength(0);
    expect(statusLine().textContent).toContain("you can view acc-view but not trade it");
  });

  it("nothing more is sent on a contract while an order's outcome is unknown", async () => {
    const { push, calls, app } = setup();
    await ready();
    push();
    const lost = order("u1", "PENDING", "2026-10-14T04:41:00+00:00", { pending: "UNCONFIRMED" });
    act(() => app.store.applyOrders("acc-a", [lost]));
    fireEvent.click(screen.getByLabelText("Buy at 25,010.00"));
    expect(posts(calls)).toHaveLength(0);
    expect(statusLine().textContent).toContain("outcome is unknown");
  });

  it("Work: click selects, again cancels, another row moves", async () => {
    const routes = {
      "DELETE /api/accounts/acc-a/orders/w1": { ...order("w1", "OPEN", "2026-10-14T04:42:00+00:00"), pending: "CANCEL" },
      "PATCH /api/accounts/acc-a/orders/w1": order("w1", "OPEN", "2026-10-14T04:42:00+00:00"),
    };
    const { push, calls, app } = setup({}, routes);
    await ready();
    push();
    const w1 = order("w1", "OPEN", "2026-10-14T04:41:00+00:00"); // BUY 75 @ 25,010.00
    act(() => app.store.applyOrders("acc-a", [w1]));

    fireEvent.click(screen.getByLabelText("Select my orders at 25,010.00"));
    fireEvent.click(screen.getByLabelText("Move selected orders to 25,009.70"));
    await vi.waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]).toMatchObject({ method: "PATCH", path: "/api/accounts/acc-a/orders/w1", body: { price_paise: 2500970 } });

    fireEvent.click(screen.getByLabelText("Select my orders at 25,010.00"));
    fireEvent.click(screen.getByLabelText("Cancel my orders at 25,010.00"));
    await vi.waitFor(() => expect(posts(calls)).toHaveLength(2));
    expect(posts(calls)[1]).toMatchObject({ method: "DELETE", path: "/api/accounts/acc-a/orders/w1" });
  });

  it("a selection whose orders are gone is dropped: a new order at that price is never selected by it", async () => {
    const { push, app } = setup();
    await ready();
    push();
    act(() => app.store.applyOrders("acc-a", [order("w1", "OPEN", "2026-10-14T04:41:00+00:00")])); // BUY 75 @ 25,010.00
    fireEvent.click(screen.getByLabelText("Select my orders at 25,010.00"));
    expect(screen.getByLabelText("Cancel my orders at 25,010.00")).toBeTruthy(); // selected: a click cancels
    act(() => app.store.applyOrders("acc-a", [order("w1", "FILLED", "2026-10-14T04:42:00+00:00", { filled_qty: 75 })])); // it filled
    act(() => app.store.applyOrders("acc-a", [order("w2", "OPEN", "2026-10-14T04:43:00+00:00")])); // a new one, same price
    expect(screen.getByLabelText("Select my orders at 25,010.00")).toBeTruthy(); // w2 was never selected: a click selects, never cancels
  });

  it("CXL ALL cancels only this contract's working orders, even while trading is stopped", async () => {
    const routes = {
      "DELETE /api/accounts/acc-a/orders/w1": order("w1", "CANCELLED", "2026-10-14T04:42:00+00:00"),
      "DELETE /api/accounts/acc-a/orders/w2": order("w2", "CANCELLED", "2026-10-14T04:42:00+00:00"),
    };
    const { push, calls, app, socket } = setup({}, routes);
    await ready();
    push();
    const other = order("x1", "OPEN", "2026-10-14T04:41:00+00:00");
    act(() =>
      app.store.applyOrders("acc-a", [
        order("w1", "OPEN", "2026-10-14T04:41:00+00:00"),
        order("w2", "PARTIAL", "2026-10-14T04:41:00+00:00", { filled_qty: 25 }),
        order("done", "FILLED", "2026-10-14T04:41:00+00:00", { filled_qty: 75 }),
        { ...other, request: { ...other.request, instrument_id: "NSE:SBIN" } },
      ]),
    );
    act(() => socket().receive({ type: "kill", scope: "account acc-a", engaged: true, reason: "break", by: "ayush", at: null }));
    fireEvent.click(screen.getByText("CXL", { exact: false }));
    await vi.waitFor(() => expect(posts(calls)).toHaveLength(2));
    expect(posts(calls).map((c) => c.path).sort()).toEqual(["/api/accounts/acc-a/orders/w1", "/api/accounts/acc-a/orders/w2"]);
  });

  it("orders over the confirm value need a second click on the same cell", async () => {
    // 2 lots × 75 × ₹25,010 = ₹37,51,500: over ₹1L
    const { push, calls } = setup({ confirmOverPaise: 1_00_000_00 });
    await ready();
    push();
    fireEvent.click(screen.getByLabelText("Buy at 25,010.00"));
    expect(posts(calls)).toHaveLength(0);
    expect(statusLine().textContent).toContain("Click again to confirm BUY 150 NIFTY26OCTFUT @ 25,010.00 (₹37,51,500.00)");
    fireEvent.click(screen.getByLabelText("Buy at 25,009.90")); // a different cell re-arms instead
    expect(posts(calls)).toHaveLength(0);
    fireEvent.click(screen.getByLabelText("Buy at 25,009.90"));
    await vi.waitFor(() => expect(posts(calls)).toHaveLength(1));
  });

  it("the server's refusal shows in the status line", async () => {
    const { push, socket } = setup();
    await ready();
    push();
    const rejected = order("r1", "REJECTED", "2026-10-14T04:41:00+00:00", { reason: "price is more than 5% from the last price" });
    act(() => socket().receive({ type: "order", account_id: "acc-a", kind: "REJECTED", detail: "", order: rejected, fill: null }));
    expect(statusLine().textContent).toContain("BUY 75 NIFTY26OCTFUT refused: price is more than 5% from the last price");
  });

  it("a lost answer to 'place' says to check the order book", async () => {
    const { push } = setup({}, { "POST /api/accounts/acc-a/orders": () => ({ status: 0 }) });
    await ready();
    push();
    fireEvent.click(screen.getByLabelText("Buy at 25,010.00"));
    await vi.waitFor(() => expect(statusLine().textContent).toContain("no answer from MockTrade, check the order book"));
  });

  it("lot buttons add, 0 and Esc clear", async () => {
    const { setParams } = setup({ lots: 2 });
    await ready();
    fireEvent.click(screen.getByText("+5"));
    expect(setParams).toHaveBeenLastCalledWith({ lots: 7 });
    fireEvent.click(screen.getByText("0", { selector: "button" }));
    expect(setParams).toHaveBeenLastCalledWith({ lots: 0 });
    fireEvent.keyDown(document.querySelector(".ladder")!, { key: "Escape" });
    expect(setParams).toHaveBeenLastCalledWith({ lots: 0 });
    expect(screen.getByText("= 150 qty")).toBeTruthy();
  });

  it("auto-centre follows a big move; scrolling turns it off", async () => {
    const { push } = setup();
    await ready();
    push();
    push({ ...BOOK, ltp_paise: 2502000, bids: [], asks: [] }); // 100 rows away
    expect(document.querySelector(".price.last")!.textContent).toBe("25,020.00");
    fireEvent.click(screen.getByLabelText("Scroll prices up"));
    expect(screen.getByText("CTR")).toBeTruthy();
    push({ ...BOOK, ltp_paise: 2504000, bids: [], asks: [] });
    expect(document.querySelector(".price.last")).toBeNull(); // off the screen: no jumping
  });

  it("offers a search when it has no contract", async () => {
    const { setInstrument } = setup({ instrumentId: undefined }, { "GET /api/instruments": [NIFTY] });
    const input = screen.getByLabelText("Ladder instrument");
    fireEvent.change(input, { target: { value: "nif" } });
    fireEvent.mouseDown(await screen.findByRole("option", { name: /NIFTY26OCTFUT/ }));
    expect(setInstrument).toHaveBeenCalledWith(ID);
  });
});
