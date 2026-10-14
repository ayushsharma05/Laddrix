import { act, fireEvent, render, screen } from "@testing-library/react";
import type { OrderView } from "../../api/types";
import { AppProvider, createApp } from "../../app/context";
import { fakeRest, fakeStream, tick } from "../../test/fakes";
import { ME, order } from "../../test/fixtures";
import { Ticket, type TicketParams } from "./Ticket";

const ID = "NFO:NIFTY26OCTFUT";
const NIFTY = { id: ID, exchange: "NFO", symbol: "NIFTY26OCTFUT", kind: "FUT", tick_paise: 10, lot_size: 75, expiry: "2026-10-27", strike_paise: null, underlying: "NIFTY", tradable: true, ltp_paise: 2501000 };

function accepted(body: Record<string, unknown>): OrderView {
  const o = order(String(body.client_order_id), "OPEN", "2026-10-14T04:41:00+00:00");
  return { ...o, request: { ...o.request, ...(body as Partial<OrderView["request"]>) } };
}

function setup(params: Partial<TicketParams> = {}, routes: Record<string, unknown> = {}) {
  const server = fakeRest({
    [`GET /api/instruments/${encodeURIComponent(ID)}`]: NIFTY,
    "POST /api/accounts/acc-a/orders": (body: unknown) => ({ status: 201, body: accepted(body as Record<string, unknown>) }),
    ...routes,
  });
  const { stream, socket } = fakeStream();
  const app = createApp({ rest: server.rest, stream });
  app.store.setMe(ME);
  stream.start();
  socket().open();
  const props = {
    params: { link: "pink", instrumentId: ID, lots: 2, type: "LMT", tif: "DAY", confirmOverPaise: 0, ...params } as TicketParams,
    setParams: vi.fn(),
    setInstrument: vi.fn(),
    publish: vi.fn(),
    open: vi.fn(),
  };
  const view = render(
    <AppProvider app={app}>
      <Ticket {...props} />
    </AppProvider>,
  );
  const rerender = (change: Partial<TicketParams>) => {
    props.params = { ...props.params, ...change };
    view.rerender(
      <AppProvider app={app}>
        <Ticket {...props} />
      </AppProvider>,
    );
  };
  act(() => socket().receive({ type: "ticks", ticks: [tick(ID, 2501000, { bids: [[2501000, 150, 2]], asks: [[2501010, 75, 1]] })] }));
  return { ...server, ...props, app, socket, rerender };
}

/** The ticket is ready once it has filled in the last price. */
async function ready(): Promise<HTMLInputElement> {
  const price = (await screen.findByLabelText("Price")) as HTMLInputElement;
  await vi.waitFor(() => expect(price.disabled || price.value === "25,010.00").toBe(true));
  return price;
}

const posts = (calls: { method: string; body: unknown }[]) => calls.filter((c) => c.method === "POST");
const send = () => screen.getByRole("button", { name: /^(BUY|SELL) / });
const status = () => (document.querySelector(".ladder-status") as HTMLElement).textContent ?? "";

describe("Order ticket", () => {
  it("starts at the last price and sends a limit order", async () => {
    const { calls } = setup();
    const price = await ready();
    expect(price.value).toBe("25,010.00");
    expect(screen.getByLabelText("Order summary").textContent).toContain("₹37,51,500.00");
    fireEvent.click(send());
    await vi.waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]!.body).toMatchObject({ side: "BUY", qty: 150, order_type: "LIMIT", price_paise: 2501000, trigger_paise: null, market_protection: null });
  });

  it("−/+ and ↑/↓ move the price one tick; Enter sends; SELL switches side", async () => {
    const { calls } = setup();
    const price = await ready();
    fireEvent.click(screen.getByLabelText("Price one tick higher"));
    expect(price.value).toBe("25,010.10");
    fireEvent.keyDown(price, { key: "ArrowDown" });
    fireEvent.keyDown(price, { key: "ArrowDown" });
    expect(price.value).toBe("25,009.90");
    fireEvent.click(screen.getByRole("button", { name: "SELL" }));
    fireEvent.keyDown(price, { key: "Enter" });
    await vi.waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]!.body).toMatchObject({ side: "SELL", price_paise: 2500990 });
  });

  it("stop-loss: price and trigger; a trigger the market already passed is refused before sending", async () => {
    const { calls } = setup({ type: "SL" });
    await ready();
    fireEvent.change(screen.getByLabelText("Price"), { target: { value: "25020" } });
    fireEvent.change(screen.getByLabelText("Trigger"), { target: { value: "25005" } });
    expect(status()).toContain("above the last price");
    expect((send() as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Trigger"), { target: { value: "25015" } });
    fireEvent.blur(screen.getByLabelText("Trigger"));
    expect((screen.getByLabelText("Trigger") as HTMLInputElement).value).toBe("25,015.00"); // tidied once typed
    fireEvent.click(send());
    await vi.waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]!.body).toMatchObject({ order_type: "SL", price_paise: 2502000, trigger_paise: 2501500 });
  });

  it("market: no price, protection, and 'pays at most' shown", async () => {
    const { calls } = setup({ type: "MKT" });
    await ready();
    expect((screen.getByLabelText("Price") as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByLabelText("Order summary").textContent).toContain("Pays at most 25,510.20");
    fireEvent.click(send());
    await vi.waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]!.body).toMatchObject({ order_type: "MARKET", price_paise: null, market_protection: -1 });
  });

  it("over the confirm value, the first click only asks; Back goes back; CONFIRM sends", async () => {
    const { calls } = setup({ confirmOverPaise: 1_00_000_00 });
    await ready();
    fireEvent.click(send());
    expect(posts(calls)).toHaveLength(0);
    expect(document.activeElement?.textContent).toBe("Back");
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    fireEvent.click(send());
    fireEvent.click(screen.getByRole("button", { name: "CONFIRM BUY 150" }));
    await vi.waitFor(() => expect(posts(calls)).toHaveLength(1));
  });

  it("never sends while stopped, view-only or with an unknown earlier outcome", async () => {
    const { calls, socket, rerender, app } = setup();
    await ready();
    act(() => socket().receive({ type: "kill", scope: "account acc-a", engaged: true, reason: "break", by: "ayush", at: null }));
    expect((send() as HTMLButtonElement).disabled).toBe(true);
    fireEvent.keyDown(screen.getByLabelText("Price"), { key: "Enter" });
    expect(status()).toContain("Trading paused: trading is stopped on acc-a");
    act(() => socket().receive({ type: "kill", scope: "account acc-a", engaged: false, reason: "", by: "ayush", at: null }));
    rerender({ accountId: "acc-view" });
    fireEvent.keyDown(screen.getByLabelText("Price"), { key: "Enter" });
    rerender({ accountId: "acc-a" });
    act(() => app.store.applyOrders("acc-a", [order("u1", "PENDING", "2026-10-14T04:41:00+00:00", { pending: "UNCONFIRMED" })]));
    fireEvent.keyDown(screen.getByLabelText("Price"), { key: "Enter" });
    expect(posts(calls)).toHaveLength(0);
    const texts = app.store.getState().messages.map((m) => m.text).join(" | ");
    expect(texts).toContain("you can view acc-view but not trade it");
    expect(texts).toContain("outcome is unknown");
  });

  it("a server refusal shows in the status line", async () => {
    setup({}, { "POST /api/accounts/acc-a/orders": (body: unknown) => ({ status: 201, body: { ...accepted(body as Record<string, unknown>), status: "REJECTED", reason: "over max order value" } }) });
    await ready();
    fireEvent.click(send());
    await vi.waitFor(() => expect(status()).toContain("refused: over max order value"));
  });

  it("worst-case position counts the position and working orders on the same side", async () => {
    const { app } = setup({ lots: 1 });
    await ready();
    act(() => {
      app.store.applyOrders("acc-a", [order("w1", "OPEN", "2026-10-14T04:41:00+00:00")]); // BUY 75 working
      app.store.applyPositions("acc-a", {
        positions: [{ instrument_id: ID, product: "NRML", qty: 150, avg_price_paise: "2500000", ltp_paise: 2501000, realised_paise: 0, unrealised_paise: 0, pnl_paise: 0, buy_qty: 150, sell_qty: 0, buy_value_paise: 0, sell_value_paise: 0, updated_at: "2026-10-14T04:41:00+00:00" }],
        realised_paise: 0, unrealised_paise: 0, day_pnl_paise: 0, reconcile: { checked_at: null, ok: null, error: "", alerts: [] },
      });
    });
    expect(screen.getByLabelText("Order summary").textContent).toContain("Worst position +300");
  });
});
