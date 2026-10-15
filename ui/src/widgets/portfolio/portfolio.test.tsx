import { act, fireEvent, render, screen, within } from "@testing-library/react";
import type { Funds, Holding, PortfolioSnapshot, Trade } from "../../api/types";
import { AppProvider, createApp } from "../../app/context";
import { fakeRest, fakeStream } from "../../test/fakes";
import { ME } from "../../test/fixtures";
import { OrderBook } from "../books/OrderBook";
import { FundsPanel } from "./FundsPanel";
import { HoldingsPanel } from "./HoldingsPanel";
import { freshness, holdingTotals, pnlPct, tradeRows } from "./model";

const REL = "NSE:RELIANCE";
const FUT = "NFO:NIFTY26OCTFUT";

const FUNDS: Funds = {
  segment: "equity",
  available_paise: 97250505,
  used_paise: 2749495,
  opening_paise: 100000000,
  collateral_paise: 0,
  available_lines: [
    { label: "Opening balance", paise: 100000000 },
    { label: "Realised P&L today", paise: 125000 },
  ],
  used_lines: [{ label: "Options premium", paise: 2749495 }],
  estimate: true,
};

function holding(extra: Partial<Holding> = {}): Holding {
  return {
    instrument_id: REL,
    qty: 10,
    t1_qty: 2,
    used_qty: 0,
    total_qty: 12,
    avg_price_paise: 245056,
    ltp_paise: 250110,
    close_paise: 249000,
    invested_paise: 12 * 245056,
    value_paise: 12 * 250110,
    pnl_paise: 12 * (250110 - 245056),
    day_change_paise: 12 * (250110 - 249000),
    ...extra,
  };
}

function trade(id: string, ts: string, extra: Partial<Trade> = {}): Trade {
  return {
    trade_id: id,
    broker_order_id: "B7",
    client_order_id: "MT1",
    instrument_id: FUT,
    side: "BUY",
    product: "NRML",
    qty: 75,
    price_paise: 2501010,
    value_paise: 75 * 2501010,
    ts,
    ...extra,
  };
}

function snap(kind: PortfolioSnapshot["kind"], items: PortfolioSnapshot["items"], extra: Partial<PortfolioSnapshot> = {}): PortfolioSnapshot {
  return { account_id: "acc-a", kind, items, as_of: "2026-10-15T05:01:05+00:00", error: "", ...extra };
}

function setup(routes: Record<string, unknown> = {}) {
  const server = fakeRest(routes);
  const { stream, socket } = fakeStream();
  const app = createApp({ rest: server.rest, stream });
  app.store.setMe(ME);
  app.store.selectAccount("acc-a");
  stream.start();
  socket().open();
  const props = { setParams: vi.fn(), setInstrument: vi.fn(), publish: vi.fn(), open: vi.fn() };
  return { ...server, app, socket, props };
}

describe("portfolio model", () => {
  it("says how old the figures are, and why a refresh failed (keeping them)", () => {
    expect(freshness(undefined, undefined).text).toBe("loading…");
    expect(freshness(snap("funds", []), undefined)).toMatchObject({ text: "as of 10:31:05", tone: "dim" });
    expect(freshness(snap("funds", [], { error: "Kite /user/margins: Session expired" }), undefined)).toMatchObject({
      text: "as of 10:31:05 · couldn't refresh: Kite /user/margins: Session expired",
      tone: "bad",
    });
    expect(freshness(snap("funds", [], { as_of: null, error: "no answer" }), undefined).text).toBe("couldn't read: no answer");
    expect(freshness(undefined, "fake doesn't report holdings").text).toBe("fake doesn't report holdings");
  });

  it("adds up holdings, with no value until every price is known", () => {
    const t = holdingTotals([holding(), holding({ instrument_id: "NSE:TCS", invested_paise: 100, value_paise: 150, day_change_paise: -5 })]);
    expect(t).toEqual({
      invested: 12 * 245056 + 100,
      value: 12 * 250110 + 150,
      pnl: 12 * 250110 + 150 - 12 * 245056 - 100,
      dayChange: 12 * (250110 - 249000) - 5,
    });
    expect(holdingTotals([holding(), holding({ value_paise: null, pnl_paise: null })]).value).toBeNull();
    expect(pnlPct(60648, 2940672)).toBe("+2.06%");
    expect(pnlPct(-1, 1_000_000)).toBe("0.00%");
    expect(pnlPct(null, 100)).toBe("");
  });

  it("lists trades newest first", () => {
    const rows = tradeRows([trade("1", "2026-10-15T04:30:01+00:00"), trade("3", "2026-10-15T04:31:00+00:00"), trade("2", "2026-10-15T04:30:01+00:00")]);
    expect(rows.map((t) => t.trade_id)).toEqual(["3", "2", "1"]);
  });
});

describe("the store", () => {
  it("keeps the newest read: a slow answer can't undo a push, a new error on the same read can", () => {
    const { app } = setup();
    const newer = snap("funds", [FUNDS], { as_of: "2026-10-15T05:02:00+00:00" });
    app.store.applyPortfolio(newer);
    app.store.applyPortfolio(snap("funds", [], { as_of: "2026-10-15T05:01:00+00:00" }));
    expect(app.store.getState().books["acc-a"]!.portfolio.funds).toBe(newer);
    app.store.applyPortfolio({ ...newer, error: "Session expired" });
    expect(app.store.getState().books["acc-a"]!.portfolio.funds!.error).toBe("Session expired");
    app.store.applyPortfolio(snap("funds", [], { as_of: null, error: "x" }));
    expect(app.store.getState().books["acc-a"]!.portfolio.funds!.items).toEqual([FUNDS]); // never emptied
  });
});

describe("Funds", () => {
  it("shows each segment's money from the server, live from the stream, and refreshes on demand", async () => {
    const { app, socket, props, calls } = setup({
      "GET /api/accounts/acc-a/funds": snap("funds", [{ ...FUNDS, available_paise: 1 }], { as_of: "2026-10-15T05:03:00+00:00" }),
    });
    render(
      <AppProvider app={app}>
        <FundsPanel params={{ link: "none" }} {...props} />
      </AppProvider>,
    );
    expect(screen.getByText("loading…")).toBeTruthy();
    act(() => socket().receive({ type: "portfolio", ...snap("funds", [FUNDS]) }));
    const segment = screen.getByRole("region", { name: "equity funds" });
    expect(segment.textContent).toContain("Available ₹9,72,505.05");
    expect(segment.textContent).toContain("Used ₹27,494.95");
    expect(within(segment).getByText("estimate")).toBeTruthy();
    expect(within(segment).getByLabelText("Used").textContent).toBe("Options premium₹27,494.95");
    expect(screen.getByText("as of 10:31:05")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await vi.waitFor(() => expect(calls.some((c) => c.path === "/api/accounts/acc-a/funds?fresh=true")).toBe(true));
    await vi.waitFor(() => expect(segment.textContent).toContain("Available ₹0.01"));
  });
});

describe("Holdings", () => {
  it("lists holdings with T1, sold-today, P&L and a total row; a click drives the link group", () => {
    const { app, props } = setup();
    render(
      <AppProvider app={app}>
        <HoldingsPanel params={{ link: "pink" }} {...props} />
      </AppProvider>,
    );
    act(() => app.store.applyPortfolio(snap("holdings", [holding({ used_qty: 3 })])));
    const rows = within(screen.getByRole("grid", { name: "Holdings" })).getAllByRole("row");
    expect(rows[0]!.textContent).toContain("RELIANCE");
    expect(rows[0]!.textContent).toContain("12 (2 T1) −3");
    expect(rows[0]!.textContent).toContain("+₹606.48 +2.06%");
    expect(rows[1]!.textContent).toContain("Total");
    fireEvent.click(rows[0]!);
    expect(props.publish).toHaveBeenCalledWith(REL);
  });

  it("says when the broker doesn't report holdings", async () => {
    const { app, props } = setup({
      "GET /api/accounts/acc-a/holdings": (() => ({ status: 404, body: { detail: "fake doesn't report holdings" } })) as unknown,
    });
    render(
      <AppProvider app={app}>
        <HoldingsPanel params={{ link: "pink" }} {...props} />
      </AppProvider>,
    );
    await act(() => app.session.loadPortfolio("acc-a", "holdings"));
    expect(screen.getByText("fake doesn't report holdings")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Refresh" }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("the order book's Trades tab", () => {
  it("shows the broker's tradebook, ours and those placed elsewhere", () => {
    const { app, props } = setup();
    render(
      <AppProvider app={app}>
        <OrderBook params={{ link: "pink", tab: "trades" }} {...props} />
      </AppProvider>,
    );
    act(() =>
      app.store.applyPortfolio(
        snap("trades", [trade("1", "2026-10-15T04:30:01+00:00"), trade("2", "2026-10-15T04:31:00+00:00", { client_order_id: "", side: "SELL" })]),
      ),
    );
    expect(screen.getByRole("button", { name: /Trades 2/ }).getAttribute("aria-pressed")).toBe("true");
    const rows = within(screen.getByRole("grid", { name: "Trades" })).getAllByRole("row");
    expect(rows[0]!.textContent).toContain("outside MockTrade");
    expect(rows[1]!.textContent).toContain("10:00:01");
    expect(rows[1]!.textContent).toContain("18,75,757.50");
    fireEvent.doubleClick(rows[1]!);
    expect(props.open).toHaveBeenCalledWith("audit", { link: "none", orderId: "MT1" });
    fireEvent.click(screen.getByRole("button", { name: /Working/ }));
    expect(props.setParams).toHaveBeenCalledWith({ tab: "working" });
  });
});
