import { act, fireEvent, render, screen } from "@testing-library/react";
import type { StrategyAnalysis } from "../../api/types";
import { AppProvider, createApp } from "../../app/context";
import { fakeRest, fakeStream } from "../../test/fakes";
import { ME } from "../../test/fixtures";
import { StrategyBuilder, type StrategyParams } from "./StrategyBuilder";

const EXP = "2026-10-27";
const CE = (k: number) => `NFO:NIFTY26OCT${k}CE`;

function analysis(legs: { instrument_id: string; side: "BUY" | "SELL"; lots: number }[]): StrategyAnalysis {
  return {
    underlying: "NIFTY",
    expiry: EXP,
    forward_paise: 2500000,
    prices: [24800, 25000, 25200],
    payoff_expiry: [-3000, 0, 4500],
    payoff_today: [-2500, 200, 4000],
    breakevens: [25000],
    max_profit: 4500,
    max_loss: -3000,
    net_premium: -3000,
    net_greeks: { delta: 30.5, gamma: 0.002, theta: -84.8, vega: 12.3 },
    legs: legs.map((l) => ({ ...l, lot_size: 75, strike_paise: Number(l.instrument_id.slice(-7, -2)) * 100, kind: "CE", expiry: EXP, price_paise: 12000, iv: 0.12 })),
  };
}

function setup(params: Partial<StrategyParams> = {}) {
  const posted: unknown[] = [];
  const server = fakeRest({
    "GET /api/options": ["NIFTY"],
    "GET /api/options/NIFTY/expiries": [EXP],
    "GET /api/options/strategies": [
      { code: "BULL_CALL_SPREAD", name: "Bull call spread", view: "a moderate rise, limited risk", legs: [], params: [] },
      { code: "IRON_CONDOR", name: "Iron condor", view: "a range", legs: [], params: [] },
    ],
    "POST /api/options/strategy/build": (body: unknown) => {
      posted.push(body);
      return { status: 200, body: analysis([{ instrument_id: CE(25000), side: "BUY", lots: 1 }, { instrument_id: CE(25200), side: "SELL", lots: 1 }]) };
    },
    "POST /api/accounts/acc-a/baskets/check": (body: unknown) => {
      posted.push(body);
      const legs = (body as { legs: { instrument_id: string; side: "BUY" | "SELL"; lots: number }[] }).legs;
      const naked = legs.some((l) => l.side === "SELL") && !legs.some((l) => l.side === "BUY");
      return {
        status: 200,
        body: {
          ok: !naked,
          legs: legs.map((l) => ({ instrument_id: l.instrument_id, side: l.side, qty: 75 * l.lots, price_paise: 12010, problem: naked ? "selling options to open a short position is not enabled" : null })),
          margin: null,
          margin_note: "this broker doesn't quote margins (paper)",
        },
      };
    },
    "POST /api/accounts/acc-a/baskets/send": (body: unknown) => {
      posted.push(body);
      return { status: 201, body: { basket_id: "B261014ABC123", sent: [], not_sent: [], stopped: "" } };
    },
    "POST /api/options/strategy/analyse": (body: unknown) => {
      const legs = (body as { legs: { instrument_id: string; side: "BUY" | "SELL"; lots: number }[] }).legs;
      if (legs.some((l) => l.instrument_id.includes("FUT"))) return { status: 422, body: { detail: "NIFTY26OCTFUT is not a listed option" } };
      return { status: 200, body: analysis(legs) };
    },
  });
  const { stream } = fakeStream();
  const app = createApp({ rest: server.rest, stream });
  app.store.setMe(ME);
  const props = { params: { link: "none" as const, width: 2, lots: 1, legs: [], ...params }, setParams: vi.fn(), setInstrument: vi.fn(), publish: vi.fn(), open: vi.fn() };
  const view = render(
    <AppProvider app={app}>
      <StrategyBuilder {...props} />
    </AppProvider>,
  );
  const rerender = (change: Partial<StrategyParams>) => {
    props.params = { ...props.params, ...change };
    view.rerender(
      <AppProvider app={app}>
        <StrategyBuilder {...props} />
      </AppProvider>,
    );
  };
  return { ...props, ...server, app, posted, rerender };
}

describe("Strategy builder", () => {
  it("builds a template on the server and saves its legs", async () => {
    const { setParams, posted } = setup();
    await screen.findByRole("option", { name: "Iron condor" });
    await screen.findByRole("option", { name: "27 Oct" });
    fireEvent.click(screen.getByRole("button", { name: "Build" }));
    await vi.waitFor(() => expect(posted).toEqual([{ code: "BULL_CALL_SPREAD", underlying: "NIFTY", expiry: EXP, width: 2, lots: 1 }]));
    await vi.waitFor(() =>
      expect(setParams).toHaveBeenCalledWith({
        legs: [
          { instrument_id: CE(25000), side: "BUY", lots: 1 },
          { instrument_id: CE(25200), side: "SELL", lots: 1 },
        ],
        underlying: "NIFTY",
        expiry: EXP,
        template: "BULL_CALL_SPREAD",
      }),
    );
  });

  it("draws the server's analysis: payoff chart, premium, max profit/loss, breakevens, Greeks", async () => {
    setup({ legs: [{ instrument_id: CE(25000), side: "BUY", lots: 1 }] });
    const figures = await screen.findByLabelText("Strategy figures");
    expect(figures.textContent).toContain("Debit ₹3,000");
    expect(figures.textContent).toContain("Max profit +₹4,500");
    expect(figures.textContent).toContain("Max loss −₹3,000");
    expect(figures.textContent).toContain("Breakevens 25,000");
    expect(screen.getByLabelText("Net Greeks").textContent).toContain("Θ −₹85/day");
    const svg = screen.getByRole("img", { name: "Payoff chart" });
    expect(svg.querySelector(".payoff-expiry")!.getAttribute("d")).toMatch(/^M/);
    expect(svg.querySelector(".payoff-today")!.getAttribute("d")).toMatch(/^M/);
    expect(screen.getByRole("row").textContent).toContain("25,000 CE · 27 Oct");
  });

  it("legs are edited in place (flip, lots, remove)", async () => {
    const { setParams } = setup({ legs: [{ instrument_id: CE(25000), side: "BUY", lots: 2 }] });
    await screen.findByLabelText("Strategy figures");
    fireEvent.click(screen.getByRole("button", { name: `Flip BUY NIFTY26OCT25000CE` }));
    expect(setParams).toHaveBeenLastCalledWith({ legs: [{ instrument_id: CE(25000), side: "SELL", lots: 2, price_paise: undefined }] });
    fireEvent.click(screen.getByRole("button", { name: "More lots NIFTY26OCT25000CE" }));
    expect(setParams).toHaveBeenLastCalledWith({ legs: [{ instrument_id: CE(25000), side: "BUY", lots: 3 }] });
    fireEvent.click(screen.getByRole("button", { name: "Remove NIFTY26OCT25000CE" }));
    expect(setParams).toHaveBeenLastCalledWith({ legs: [] });
  });

  it("a refusal from the server is shown, not a chart", async () => {
    setup({ legs: [{ instrument_id: "NFO:NIFTY26OCTFUT", side: "BUY", lots: 1 }] });
    expect((await screen.findByRole("alert")).textContent).toContain("is not a listed option");
    expect(screen.queryByRole("img", { name: "Payoff chart" })).toBeNull();
  });

  it("takes legs Alt-clicked in the option chain", async () => {
    const { app, setParams } = setup();
    expect(app.store.hasStrategyBuilder).toBe(true);
    act(() => app.store.sendToStrategy({ instrument_id: CE(25100), side: "SELL", lots: 1 }));
    await vi.waitFor(() => expect(setParams).toHaveBeenCalledWith({ legs: [{ instrument_id: CE(25100), side: "SELL", lots: 1 }] }));
    expect(app.store.getState().strategyInbox).toEqual([]);
  });

  it("trades the legs as a basket: check first, then send; a failed check sends nothing", async () => {
    const { posted, app } = setup({ legs: [{ instrument_id: CE(25000), side: "BUY", lots: 1 }, { instrument_id: CE(25200), side: "SELL", lots: 1 }] });
    app.store.setStream("open");
    await screen.findByLabelText("Strategy figures");
    expect(screen.queryByRole("button", { name: /^Send/ })).toBeNull(); // nothing to send before a check
    fireEvent.change(screen.getByRole("combobox", { name: "Price" }), { target: { value: "mid" } });
    fireEvent.click(screen.getByRole("button", { name: "Check" }));
    expect((await screen.findByRole("status")).textContent).toContain("this broker doesn't quote margins");
    fireEvent.click(screen.getByRole("button", { name: "Send 2 orders" }));
    await vi.waitFor(() => expect(app.store.getState().messages.some((m) => m.text === "Basket B261014ABC123: 0 orders sent")).toBe(true));
    expect(posted.at(-1)).toEqual({
      legs: [
        { instrument_id: CE(25000), side: "BUY", lots: 1, product: "NRML", at: "mid" },
        { instrument_id: CE(25200), side: "SELL", lots: 1, product: "NRML", at: "mid" },
      ],
    });
  });

  it("a leg that fails the check is named, and there is no Send", async () => {
    const { app } = setup({ legs: [{ instrument_id: CE(25200), side: "SELL", lots: 1 }] });
    app.store.setStream("open");
    await screen.findByLabelText("Strategy figures");
    fireEvent.click(screen.getByRole("button", { name: "Check" }));
    const status = await screen.findByRole("status");
    expect(status.textContent).toContain("not enabled");
    expect(status.textContent).toContain("Nothing can be sent until every leg passes.");
    expect(screen.queryByRole("button", { name: /^Send/ })).toBeNull();
  });
});
