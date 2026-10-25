import { act, fireEvent, render, screen, within } from "@testing-library/react";
import type { ChainLeg, OptionChain as Chain } from "../../api/types";
import { AppProvider, createApp } from "../../app/context";
import { fakeRest, fakeStream } from "../../test/fakes";
import { ME } from "../../test/fixtures";
import { OptionChain, type ChainParams } from "./OptionChain";

const EXP = "2026-10-27";

function leg(id: string, ltp: number, oi: number, delta: number): ChainLeg {
  return { instrument_id: id, ltp_paise: ltp, bid_paise: ltp - 5, ask_paise: ltp + 5, volume: 1000, oi, oi_change: 0, iv: 0.12, delta, gamma: 0.0004, theta: -12, vega: 9 };
}

function chain(forward = 2500995): Chain & { type: "chain" } {
  const rows = [2490000, 2500000, 2510000].map((k) => ({
    strike_paise: k,
    call: leg(`NFO:NIFTY26OCT${k / 100}CE`, 25000 - (k - 2500000) / 50, k === 2510000 ? 90000 : 30000, 0.5),
    put: leg(`NFO:NIFTY26OCT${k / 100}PE`, 22000 + (k - 2500000) / 50, 20000, -0.5),
  }));
  return { type: "chain", underlying: "NIFTY", expiry: EXP, forward_paise: forward, atm_strike_paise: 2500000, days_to_expiry: 12.7, rate: 0.065, pcr: 0.83, max_pain_paise: 2500000, atm_iv: 0.12, rows };
}

function setup(params: Partial<ChainParams> = {}) {
  const server = fakeRest({ "GET /api/options": ["BANKNIFTY", "NIFTY"], "GET /api/options/NIFTY/expiries": [EXP, "2026-11-24"], "GET /api/options/BANKNIFTY/expiries": [EXP] });
  const { stream, socket } = fakeStream();
  const app = createApp({ rest: server.rest, stream });
  app.store.setMe(ME);
  stream.start();
  socket().open();
  const props = { params: { link: "pink" as const, strikes: 10, columns: ["oi", "iv", "delta", "ltp"] as ChainParams["columns"], underlying: "NIFTY", ...params }, setParams: vi.fn(), setInstrument: vi.fn(), publish: vi.fn(), open: vi.fn() };
  const view = render(
    <AppProvider app={app}>
      <OptionChain {...props} />
    </AppProvider>,
  );
  const rerender = (change: Partial<ChainParams>) =>
    view.rerender(
      <AppProvider app={app}>
        <OptionChain {...props} params={{ ...props.params, ...change }} />
      </AppProvider>,
    );
  return { ...props, socket, stream, rerender };
}

const sent = (socket: () => { sent: unknown[] }) => socket().sent as Record<string, unknown>[];
const subscribed = (socket: () => { sent: unknown[] }) => vi.waitFor(() => expect(sent(socket).some((m) => m.op === "chain")).toBe(true));

describe("Option chain widget", () => {
  it("asks the server for the chain and draws the user's columns, LTP next to the strike", async () => {
    const { socket } = setup();
    await vi.waitFor(() => expect(sent(socket)).toContainEqual({ op: "chain", underlying: "NIFTY", expiry: EXP, strikes: 10 }));
    act(() => socket().receive(chain()));
    const atm = screen.getByRole("row", { name: "Strike 25,000 (ATM)" });
    const [calls, puts] = within(atm).getAllByRole("button");
    expect([...calls!.querySelectorAll("span")].map((s) => s.textContent)).toEqual(["30K", "12.00", "0.50", "250.00"]);
    expect([...puts!.querySelectorAll("span")].map((s) => s.textContent)).toEqual(["220.00", "-0.50", "12.00", "20K"]);
    expect(screen.getByLabelText("Chain summary").textContent).toContain("PCR 0.83");
    expect(screen.getByLabelText("Chain summary").textContent).toContain("Max pain 25,000");
  });

  it("shades in-the-money sides and draws OI bars relative to the largest", async () => {
    const { socket } = setup();
    await subscribed(socket);
    act(() => socket().receive(chain()));
    const low = screen.getByRole("row", { name: "Strike 24,900" });
    const high = screen.getByRole("row", { name: "Strike 25,100" });
    expect(within(low).getByRole("button", { name: "Call 24,900" }).className).toContain("itm");
    expect(within(low).getByRole("button", { name: "Put 24,900" }).className).not.toContain("itm");
    expect(within(high).getByRole("button", { name: "Put 25,100" }).className).toContain("itm");
    expect(within(high).getByRole("button", { name: "Call 25,100" }).style.getPropertyValue("--oi")).toBe("100%");
    expect(within(low).getByRole("button", { name: "Call 24,900" }).style.getPropertyValue("--oi")).toBe("33%");
  });

  it("a click sends the option to the link group; arrows and Enter do too", async () => {
    const { socket, publish } = setup();
    await subscribed(socket);
    act(() => socket().receive(chain()));
    fireEvent.click(screen.getByRole("button", { name: "Put 25,000" }));
    expect(publish).toHaveBeenLastCalledWith("NFO:NIFTY26OCT25000PE");
    const grid = screen.getByRole("grid", { name: "Option chain" });
    fireEvent.keyDown(grid, { key: "ArrowDown" });
    fireEvent.keyDown(grid, { key: "ArrowLeft" });
    fireEvent.keyDown(grid, { key: "Enter" });
    expect(publish).toHaveBeenLastCalledWith("NFO:NIFTY26OCT25100CE");
  });

  it("expiry tabs and strikes are saved in the widget's settings; leaving stops the chain", async () => {
    const { setParams } = setup();
    fireEvent.click(await screen.findByRole("button", { name: "24 Nov" }));
    expect(setParams).toHaveBeenCalledWith({ expiry: "2026-11-24" });
    fireEvent.click(screen.getByRole("button", { name: "More strikes" }));
    expect(setParams).toHaveBeenCalledWith({ strikes: 12 });
    fireEvent.change(screen.getByRole("combobox", { name: "Underlying" }), { target: { value: "BANKNIFTY" } });
    expect(setParams).toHaveBeenCalledWith({ underlying: "BANKNIFTY", expiry: undefined });
  });

  it("another underlying never offers the previous one's expiry dates while its own load", async () => {
    const { rerender } = setup();
    await screen.findByRole("button", { name: "24 Nov" }); // NIFTY's
    rerender({ underlying: "FINNIFTY" }); // the server doesn't answer for it (404)
    await act(async () => {});
    expect(screen.queryByRole("button", { name: "24 Nov" })).toBeNull();
    expect(screen.queryByRole("button", { name: "27 Oct" })).toBeNull();
  });

  it("the column picker changes what's shown", async () => {
    const { socket, setParams } = setup();
    await subscribed(socket);
    act(() => socket().receive(chain()));
    fireEvent.click(screen.getByRole("button", { name: "Columns ▾" }));
    fireEvent.click(within(screen.getByRole("group", { name: "Columns" })).getByLabelText("Θ"));
    expect(setParams).toHaveBeenCalledWith({ columns: ["oi", "iv", "delta", "ltp", "theta"] });
  });
});

describe("stream chain subscriptions", () => {
  it("widgets share one server chain, re-asked after a reconnect, stopped when the last leaves", () => {
    const { stream, socket, timers } = fakeStream();
    stream.start();
    socket().open();
    const a = vi.fn();
    const b = vi.fn();
    const stopA = stream.subscribeChain({ underlying: "NIFTY", expiry: EXP, strikes: 10 }, a);
    const stopB = stream.subscribeChain({ underlying: "NIFTY", expiry: EXP, strikes: 10 }, b);
    expect(sent(socket).filter((m) => m.op === "chain")).toHaveLength(1);
    socket().receive(chain());
    expect(a).toHaveBeenCalledTimes(1);
    expect(b).toHaveBeenCalledTimes(1);
    socket().receive({ ...chain(), expiry: "2026-11-24" }); // someone else's chain
    expect(a).toHaveBeenCalledTimes(1);
    socket().drop();
    timers.at(-1)!.fn(); // reconnect
    socket().open();
    expect(sent(socket)).toContainEqual({ op: "chain", underlying: "NIFTY", expiry: EXP, strikes: 10 });
    stopA();
    expect(sent(socket).some((m) => m.op === "chain_stop")).toBe(false); // b still watches
    stopB();
    expect(sent(socket)).toContainEqual({ op: "chain_stop", underlying: "NIFTY", expiry: EXP });
  });
});
