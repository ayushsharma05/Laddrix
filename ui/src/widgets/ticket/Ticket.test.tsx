import { act, fireEvent, render, screen } from "@testing-library/react";
import type { OrderView } from "../../api/types";
import { AppProvider, createApp } from "../../app/context";
import { fakeRest, fakeStream, tick } from "../../test/fakes";
import { ME, order, orderSpec } from "../../test/fixtures";
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
    [`GET /api/accounts/acc-a/instruments/${encodeURIComponent(ID)}/order-spec`]: orderSpec(ID),
    [`GET /api/accounts/acc-view/instruments/${encodeURIComponent(ID)}/order-spec`]: orderSpec(ID),
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

// orders sent (the charge estimate is a POST too, but sends nothing to the market)
const posts = (calls: { method: string; path: string; body: unknown }[]) => calls.filter((c) => c.method === "POST" && c.path.endsWith("/orders"));
const send = () => screen.getByRole("button", { name: /^(BUY|SELL) / });
const status = () => (document.querySelector(".ladder-status") as HTMLElement).textContent ?? "";

describe("Order ticket", () => {
  it("shows the margin the order would block against what's available, red when short", async () => {
    setup({}, {
      "POST /api/accounts/acc-a/margin": {
        exit: false,
        required_paise: 22507920,
        available_paise: 15000000,
        enough: false,
        note: "",
        checked_before_sending: true,
      },
    });
    await ready();
    const line = await screen.findByText("Margin ≈ ₹2,25,079.20 of ₹1,50,000.00");
    expect(line.className).toContain("tone-bad");
    expect(line.getAttribute("title")).toBe("Needs more than is available: it will be refused before sending");
  });

  it("shows what the order would pay in charges, asked from the server", async () => {
    const { calls } = setup({}, {
      "POST /api/accounts/acc-a/charges/estimate": {
        total_paise: 4283,
        turnover_paise: 375150000,
        lines: [{ label: "Brokerage", paise: 2000 }, { label: "Stamp duty", paise: 2283 }, { label: "STT", paise: 0 }],
        by_position: {},
        schedule: { name: "Kite standard plan", checked_on: "2026-10-15", source: "" },
      },
    });
    await ready();
    const summary = screen.getByLabelText("Order summary");
    await vi.waitFor(() => expect(summary.textContent).toContain("Charges ≈ ₹42.83"));
    const asked = calls.filter((c) => c.path.endsWith("/charges/estimate"));
    expect(asked.at(-1)!.body).toEqual({ instrument_id: ID, side: "BUY", qty: 150, price_paise: 2501000, product: "NRML" });
    const tip = screen.getByText("Charges ≈", { exact: false }).getAttribute("title")!;
    expect(tip).toBe("Brokerage 20.00 · Stamp duty 22.83 (Kite standard plan, rates checked 2026-10-15)");
    expect(posts(calls)).toHaveLength(0); // an estimate sends nothing
  });

  it("draws its order types from the server's spec, so a new type needs no UI change", async () => {
    const spec = orderSpec(ID);
    const iceberg = { ...spec.order_types[0]!, code: "ICEBERG", label: "ICE", name: "Iceberg", validities: ["DAY"] };
    setup({ type: "ICEBERG" }, { [`GET /api/accounts/acc-a/instruments/${encodeURIComponent(ID)}/order-spec`]: { ...spec, order_types: [...spec.order_types, iceberg] } });
    await ready();
    expect(screen.getByRole("group", { name: "Order type" }).textContent).toBe("LMTMKTSLSL-MICE");
    expect(screen.getByRole("button", { name: "ICE" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("shows only what the broker offers: no stops, no stop buttons", async () => {
    const spec = orderSpec(ID);
    setup({}, { [`GET /api/accounts/acc-a/instruments/${encodeURIComponent(ID)}/order-spec`]: { ...spec, order_types: spec.order_types.slice(0, 2) } });
    await ready();
    expect(screen.getByRole("group", { name: "Order type" }).textContent).toBe("LMTMKT");
  });

  it("warns on the contract's expiry day, in the server's words (#290)", async () => {
    const note = "Expires today at 15:30 on 27 Oct 2026; settles in cash at the final settlement price";
    const expiry = { expires_at: "2026-10-27T15:30:00+05:30", settlement: "cash" as const, expires_today: true, expired: false, opening_cutoff: null, opening_closed: false, note };
    setup({}, { [`GET /api/accounts/acc-a/instruments/${encodeURIComponent(ID)}/order-spec`]: orderSpec(ID, { expiry }) });
    await ready();
    expect(screen.getByRole("note").textContent).toBe(note);
  });

  it("shows no expiry line on other days", async () => {
    setup();
    await ready();
    expect(screen.queryByRole("note")).toBeNull();
  });

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

  it("uses the user's own keys (K13.4): send on Ctrl+Enter instead of Enter, Alt+S for the sell side", async () => {
    const { calls, app } = setup({}, { "PUT /api/me/settings/keys": { name: "keys" } });
    const price = await ready();
    await act(() => app.keys.save({ "ticket-send": ["Ctrl+Enter"] }));
    expect(screen.getByText("Ctrl+Enter sends · Esc clears · ↑↓ one tick")).toBeTruthy(); // the hint follows
    fireEvent.keyDown(price, { key: "Enter" });
    expect(posts(calls)).toHaveLength(0); // Enter no longer sends
    fireEvent.keyDown(price, { key: "s", code: "KeyS", altKey: true });
    expect(screen.getByRole("button", { name: "SELL" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.keyDown(price, { key: "b", code: "KeyB", altKey: true });
    expect(screen.getByRole("button", { name: "BUY" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.keyDown(price, { key: "s", code: "KeyS", altKey: true });
    fireEvent.keyDown(price, { key: "Enter", ctrlKey: true });
    await vi.waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]!.body).toMatchObject({ side: "SELL" });
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
    await ready(); // the view-only account's spec loads first
    fireEvent.keyDown(screen.getByLabelText("Price"), { key: "Enter" });
    rerender({ accountId: "acc-a" });
    await ready();
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

  it("has Order | Algo | GTT tabs: Algo starts an algo here, GTT appears only when the broker holds GTTs", async () => {
    const SLICER = { code: "SLICER", name: "Slicer", help: "slices", family: "execution", params: [] };
    const TRAIL = { code: "TRAILING_STOP", name: "Trailing stop", help: "held by MockTrade", family: "protection", params: [] };
    const { setParams, rerender } = setup({}, { "GET /api/accounts/acc-a/algo-specs": [SLICER, TRAIL] });
    const tabs = () => screen.getAllByRole("tab").map((t) => t.textContent);
    await vi.waitFor(() => expect(tabs()).toEqual(["Order", "Algo"])); // no GTT: the spec answers 404
    fireEvent.click(screen.getByRole("tab", { name: "Algo" }));
    expect(setParams).toHaveBeenCalledWith({ mode: "algo" });
    rerender({ mode: "algo" });
    const form = await screen.findByRole("form", { name: "Start an algo" });
    expect(form.textContent).toContain("NIFTY26OCTFUT"); // the ticket's contract
    expect([...form.querySelectorAll("optgroup")].map((g) => g.label)).toEqual(["Execution: work an order", "Protection: held by MockTrade"]);
    expect((screen.getByRole("textbox", { name: "Lots" }) as HTMLInputElement).value).toBe("2"); // the ticket's lots
  });

  it("shows the GTT tab and form when the broker publishes a GTT spec", async () => {
    const spec = { instrument_id: ID, kinds: ["single"], legs: { single: 1 }, labels: { single: "Single" }, tick_paise: 10, lot_size: 75, products: ["NRML"], min_distance_pct: 0.25, expiry_days: 365, max_active: 250, help: {} };
    const { rerender } = setup({}, { "GET /api/accounts/acc-a/gtt-spec": spec, "GET /api/accounts/acc-a/algo-specs": [] });
    await vi.waitFor(() => expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Order", "Algo", "GTT"]));
    rerender({ mode: "gtt" });
    expect(await screen.findByRole("button", { name: /Place GTT BUY 150 NIFTY26OCTFUT/ })).toBeTruthy(); // the ticket's side and lots
  });

  it("takes a price picked on a linked chart: snapped to the tick, a priced type, said in the note", async () => {
    const { app, setParams, rerender, calls } = setup({ type: "MKT" });
    await screen.findByLabelText("Price");
    act(() => app.links.pickPrice("pink", ID, 2499987));
    expect(setParams).toHaveBeenCalledWith({ type: "LIMIT" }); // a market order has no price
    rerender({ type: "LIMIT" }); // (the workspace saves the change; here by hand)
    await vi.waitFor(() => expect((screen.getByLabelText("Price") as HTMLInputElement).value).toBe("24,999.90"));
    expect(screen.getByText("Price 24,999.90 from the chart: check it, then send")).toBeTruthy();
    act(() => app.links.pickPrice("blue", ID, 2400000)); // another group: not this ticket's
    act(() => app.links.pickPrice("pink", "NSE:RELIANCE", 250000)); // another instrument
    expect((screen.getByLabelText("Price") as HTMLInputElement).value).toBe("24,999.90");
    expect(posts(calls)).toHaveLength(0); // a pick never sends anything
  });

  it("a ticket that switches instrument for a pick takes it; an old pick isn't taken", async () => {
    const fresh = setup();
    act(() => fresh.app.links.pickPrice("pink", ID, 2500500)); // made just before the ticket showed it
    await vi.waitFor(() => expect((screen.getByLabelText("Price") as HTMLInputElement).value).toBe("25,005.00"));
    expect(fresh.app.links.freshPick("pink", Date.now() + 5001)).toBeUndefined();
    expect(fresh.app.links.freshPick("pink")?.pricePaise).toBe(2500500);
    expect(fresh.app.links.freshPick("none")).toBeUndefined();
  });

  it("switching contract starts the price at the new contract's price, never the old one's", async () => {
    const NOV = "NFO:NIFTY26NOVFUT";
    const { app, rerender } = setup({}, {
      [`GET /api/instruments/${encodeURIComponent(NOV)}`]: { ...NIFTY, id: NOV, symbol: "NIFTY26NOVFUT", ltp_paise: 2514800 },
      [`GET /api/accounts/acc-a/instruments/${encodeURIComponent(NOV)}/order-spec`]: orderSpec(NOV),
    });
    await ready();
    await app.instruments.get(NOV); // known already (as when a watch list has shown it)
    await app.specs.get("acc-a", NOV);
    rerender({ instrumentId: NOV });
    await vi.waitFor(() => expect(screen.getByText("NIFTY26NOVFUT")).toBeTruthy());
    await vi.waitFor(() => expect((screen.getByLabelText("Price") as HTMLInputElement).value).toBe("25,148.00"));
  });
});

