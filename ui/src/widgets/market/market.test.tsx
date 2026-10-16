import { act, fireEvent, render, screen, within } from "@testing-library/react";
import type { MoversAnswer, PrintsAnswer } from "../../api/types";
import { AppProvider, createApp } from "../../app/context";
import { fakeRest, fakeStream, tick } from "../../test/fakes";
import { ME } from "../../test/fixtures";
import { DepthWindow } from "./Depth";
import { Movers } from "./Movers";
import { TimeAndSales } from "./TimeAndSales";

const FUT = "NFO:NIFTY26OCTFUT";
const ENC = encodeURIComponent(FUT);
const NIFTY = { id: FUT, exchange: "NFO", symbol: "NIFTY26OCTFUT", kind: "FUT", tick_paise: 10, lot_size: 75, expiry: "2026-10-27", strike_paise: null, underlying: "NIFTY", tradable: true, ltp_paise: 2501000 };

// eslint-disable-next-line @typescript-eslint/no-explicit-any -- each widget takes its own params
function mount(node: (p: any) => React.ReactNode, routes: Record<string, unknown>, params: Record<string, unknown> = {}) {
  const server = fakeRest({ [`GET /api/instruments/${ENC}`]: NIFTY, ...routes });
  const { stream, socket } = fakeStream();
  const app = createApp({ rest: server.rest, stream });
  app.store.setMe(ME);
  app.store.selectAccount("acc-a");
  stream.start();
  socket().open();
  const p = props(params);
  render(<AppProvider app={app}>{node(p)}</AppProvider>);
  return { ...server, ...p, socket, app };
}

function props(params: Record<string, unknown> = {}) {
  return { params: { link: "pink" as const, instrumentId: FUT, ...params }, setParams: vi.fn(), setInstrument: vi.fn(), publish: vi.fn(), open: vi.fn() };
}

describe("Depth", () => {
  it("shows the levels, the totals, the imbalance and the spread, live", async () => {
    const { socket } = mount((p) => <DepthWindow {...p} />, {});
    await screen.findByText("Waiting for prices…");
    expect(socket().sent).toContainEqual({ op: "subscribe", instruments: [FUT] });
    act(() =>
      socket().receive({ type: "ticks", ticks: [tick(FUT, 2501000, { bids: [[2500990, 150, 2], [2500980, 600, 4]], asks: [[2501010, 300, 3]], buy_qty: 9000, sell_qty: 3000 })] }),
    );
    const rows = within(await screen.findByRole("grid", { name: "Depth" })).getAllByRole("row");
    expect(rows.map((r) => r.textContent)).toEqual(["215025,009.9025,010.103003", "460025,009.80"]);
    const foot = screen.getByLabelText("Depth totals").textContent;
    expect(foot).toContain("Shown: bids 750 · asks 300");
    expect(foot).toContain("Book: bids 9,000 · asks 3,000");
    expect(foot).toContain("75% bids");
    expect(foot).toContain("Spread 0.20 (2 ticks)");
    expect(screen.getByText("2 levels: what the broker sends")).toBeTruthy();
  });

  it("asks for a contract when it has none", () => {
    mount((p) => <DepthWindow {...p} />, {}, { instrumentId: undefined });
    expect(screen.getByText("Pick a contract for the depth window.")).toBeTruthy();
  });
});

describe("Time & sales", () => {
  it("polls the server's prints after the newest it has, newest first, filtered by size", async () => {
    let n = 0;
    const answer = (prints: PrintsAnswer["prints"]): PrintsAnswer => ({ instrument_id: FUT, prints, kept: 300, watched: true, estimate: "estimated from the touch" });
    const { calls, setParams, socket } = mount((p) => <TimeAndSales {...p} />, {
      [`GET /api/instruments/${ENC}/prints`]: () => {
        n += 1;
        if (n === 1)
          return {
            status: 200,
            body: answer([
              { seq: 1, t: "2026-10-16T04:00:01+00:00", price_paise: 2501010, qty: 75, side: "BUY" },
              { seq: 2, t: "2026-10-16T04:00:02+00:00", price_paise: 2500990, qty: 1500, side: "SELL" },
            ]),
          };
        return { status: 200, body: answer(n === 2 ? [{ seq: 3, t: null, price_paise: 2501000, qty: 75, side: "" }] : []) };
      },
    });
    expect(socket().sent).toContainEqual({ op: "subscribe", instruments: [FUT] }); // watched: the server keeps its trades
    const grid = await screen.findByRole("grid", { name: "Time and sales" });
    await vi.waitFor(() => expect(within(grid).getAllByRole("row")).toHaveLength(2));
    expect(within(grid).getAllByRole("row")[0]!.textContent).toBe("09:30:0225,009.901,500SELL");
    await vi.waitFor(() => expect(within(grid).getAllByRole("row")).toHaveLength(3), { timeout: 2500 });
    expect(within(grid).getAllByRole("row")[0]!.textContent).toBe("–25,010.0075–");
    expect(calls.filter((c) => c.path.includes("/prints")).map((c) => c.path.split("?")[1]).slice(0, 2)).toEqual(["after=0&limit=300", "after=2&limit=300"]);
    expect(screen.getByText("sides estimated").getAttribute("title")).toBe("estimated from the touch");
    fireEvent.change(screen.getByLabelText("Min qty"), { target: { value: "1,000" } });
    expect(setParams).toHaveBeenCalledWith({ minQty: 1000 });
  }, 10_000);

  it("the size filter hides small trades and the totals follow", async () => {
    mount((p) => <TimeAndSales {...p} />, {
      [`GET /api/instruments/${ENC}/prints`]: {
        instrument_id: FUT,
        prints: [
          { seq: 1, t: null, price_paise: 1, qty: 75, side: "BUY" },
          { seq: 2, t: null, price_paise: 1, qty: 1500, side: "SELL" },
        ],
        kept: 300,
        watched: true,
        estimate: "",
      },
    }, { minQty: 1000 });
    const grid = await screen.findByRole("grid", { name: "Time and sales" });
    await vi.waitFor(() => expect(within(grid).getAllByRole("row")).toHaveLength(1));
    expect(screen.getByText(/Shown: bought/).textContent).toBe("Shown: bought 0 · sold 1,500");
  });
});

describe("Movers", () => {
  const ANSWER = (by: MoversAnswer["by"]): MoversAnswer => ({
    by,
    rows: [
      { instrument_id: "NSE:RELIANCE", symbol: "RELIANCE", ltp_paise: 140200, change_pct: 2.1, volume: 5_100_000, oi: null, oi_change_pct: null },
      { instrument_id: FUT, symbol: "NIFTY26OCTFUT", ltp_paise: 2501000, change_pct: -0.5, volume: 21000, oi: 1050000, oi_change_pct: 5 },
    ],
    of: 72,
    note: by === "oi" ? "OI change since the first open interest seen today" : "",
    exchanges: ["NFO", "NSE"],
    kinds: ["EQ", "FUT"],
  });

  it("the server's ranking, its filters, and a click switches the link group", async () => {
    const { calls, publish, setParams } = mount((p) => <Movers {...p} />, { "GET /api/market/movers": ANSWER("gainers") }, { by: "gainers" });
    const grid = await screen.findByRole("grid", { name: "Movers" });
    await vi.waitFor(() => expect(within(grid).getAllByRole("row")).toHaveLength(2));
    expect(within(grid).getAllByRole("row")[0]!.textContent).toBe("RELIANCE1,402.00+2.10%51L");
    expect(within(grid).getAllByRole("row")[1]!.textContent).toBe("NIFTY26OCTFUT25,010.00−0.50%21K");
    expect(calls[calls.length - 1]!.path).toBe("/api/market/movers?by=gainers&limit=20&exchange=&kind=");
    expect(screen.getByText("of 72 with prices")).toBeTruthy();
    expect(within(screen.getByLabelText("Exchange")).getAllByRole("option").map((o) => o.textContent)).toEqual(["all exchanges", "NFO", "NSE"]);
    fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "FUT" } });
    expect(setParams).toHaveBeenCalledWith({ kind: "FUT" });
    fireEvent.click(screen.getByRole("button", { name: "Volume" }));
    expect(setParams).toHaveBeenCalledWith({ by: "volume" });
    fireEvent.click(within(grid).getAllByRole("row")[1]!);
    expect(publish).toHaveBeenCalledWith(FUT);
  });

  it("the OI tab shows the change and says what it is measured from", async () => {
    mount((p) => <Movers {...p} />, { "GET /api/market/movers": ANSWER("oi") }, { by: "oi" });
    const grid = await screen.findByRole("grid", { name: "Movers" });
    await vi.waitFor(() => expect(within(grid).getAllByRole("row")).toHaveLength(2));
    expect(within(grid).getAllByRole("row")[1]!.textContent).toBe("NIFTY26OCTFUT25,010.00−0.50%+5.00%");
    expect(screen.getByText("OI change since the first open interest seen today")).toBeTruthy();
  });
});
