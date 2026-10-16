import { act, fireEvent, render, screen, within } from "@testing-library/react";
import type { CandlesAnswer, ChartSpec } from "../../api/types";
import { AppProvider, createApp } from "../../app/context";
import { fakeRest, fakeStream } from "../../test/fakes";
import { ME } from "../../test/fixtures";
import { Chart, resetChartSpec } from "./Chart";
import { candle } from "./testdata";

const FUT = "NFO:NIFTY26OCTFUT";
const ENC = encodeURIComponent(FUT);
const NIFTY = { id: FUT, exchange: "NFO", symbol: "NIFTY26OCTFUT", kind: "FUT", tick_paise: 10, lot_size: 75, expiry: "2026-10-27", strike_paise: null, underlying: "NIFTY", tradable: true, ltp_paise: 2501000 };
const SPEC: ChartSpec = {
  intervals: [
    { code: "1m", label: "1 minute", seconds: 60, max_range_days: 5, default_days: 1 },
    { code: "5m", label: "5 minutes", seconds: 300, max_range_days: 30, default_days: 3 },
    { code: "1d", label: "1 day", seconds: 86400, max_range_days: 3650, default_days: 365 },
  ],
  history: "recorded ticks",
  stream_every_s: 1,
};
const at = (hhmm: string, day = "2026-10-16") => `${day}T${hhmm}:00+05:30`;
const TODAY: CandlesAnswer = {
  instrument_id: FUT,
  interval: "5m",
  candles: [candle(at("09:15"), 2500000, 2501000, 2499000, 2500500, 7500), candle(at("09:20"), 2500500, 2502000, 2500000, 2501500, 1500)],
  live_from: at("09:20"),
  note: null,
};
const OLDER: CandlesAnswer = { ...TODAY, candles: [candle(at("15:25", "2026-10-15"), 2490000), candle(at("09:15"), 1)] };

function setup(routes: Record<string, unknown> = {}, params: Record<string, unknown> = {}) {
  resetChartSpec();
  const server = fakeRest({
    "GET /api/charts/spec": SPEC,
    [`GET /api/charts/${ENC}/candles?interval=5m`]: TODAY,
    [`GET /api/charts/${ENC}/candles`]: { ...OLDER, candles: [] }, // older pages: none by default
    [`GET /api/instruments/${ENC}`]: NIFTY,
    [`GET /api/accounts/acc-a/charts/${ENC}/marks`]: { account_id: "acc-a", instrument_id: FUT, marks: [] },
    ...routes,
  });
  const { stream, socket } = fakeStream();
  const app = createApp({ rest: server.rest, stream });
  app.store.setMe(ME);
  app.store.selectAccount("acc-a");
  stream.start();
  socket().open();
  const props = { params: { link: "pink" as const, instrumentId: FUT, interval: "5m", ...params }, setParams: vi.fn(), setInstrument: vi.fn(), publish: vi.fn(), open: vi.fn() };
  render(
    <AppProvider app={app}>
      <Chart {...props} />
    </AppProvider>,
  );
  return { ...server, ...props, app, socket };
}

const readout = () => screen.getByRole("status", { name: "Candle" });
const chartImg = () => screen.getByRole("img");

// jsdom has no canvas: the chart paints nothing and everything else still works (draw.test.ts
// checks the painting with a recording context)
beforeAll(() => {
  HTMLCanvasElement.prototype.getContext = (() => null) as unknown as HTMLCanvasElement["getContext"];
});

describe("Chart", () => {
  it("draws the server's intervals and the newest candle; the forming candle comes live", async () => {
    const { socket, setParams } = setup();
    const group = await screen.findByRole("group", { name: "Interval" });
    expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual(["1m", "5m", "1d"]);
    expect(within(group).getByRole("button", { name: "5m" }).getAttribute("aria-pressed")).toBe("true");
    await vi.waitFor(() => expect(readout().textContent).toContain("C 25,015.00"));
    expect(readout().textContent).toBe("10-16 09:20 O 25,005.00 H 25,020.00 L 25,000.00 C 25,015.00 V 1.5K");
    expect(chartImg().getAttribute("aria-label")).toBe("NIFTY26OCTFUT 5m chart: 2 of 2 candles, last 25,015.00");
    expect(socket().sent).toContainEqual({ op: "candles", instrument_id: FUT, interval: "5m" });
    act(() => socket().receive({ type: "candle", instrument_id: FUT, interval: "5m", candle: candle(at("09:20"), 2500500, 2503000, 2500000, 2502500, 3000) }));
    expect(readout().textContent).toContain("C 25,025.00");
    act(() => socket().receive({ type: "candle", instrument_id: FUT, interval: "5m", candle: candle(at("09:25"), 2502500) }));
    expect(chartImg().getAttribute("aria-label")).toContain("3 of 3 candles");
    fireEvent.click(within(group).getByRole("button", { name: "1m" }));
    expect(setParams).toHaveBeenCalledWith({ interval: "1m" });
  });

  it("older candles load once the view nears the left edge, until there are none", async () => {
    const { calls } = setup({ [`GET /api/charts/${ENC}/candles`]: OLDER });
    await vi.waitFor(() => expect(chartImg().getAttribute("aria-label")).toContain("3 of 3 candles")); // one older, one already had
    const older = calls.filter((c) => c.path.includes("&end="));
    expect(older[0]!.path).toBe(`/api/charts/${ENC}/candles?interval=5m&start=${encodeURIComponent("2026-10-13T03:45:00.000Z")}&end=${encodeURIComponent(at("09:15"))}`);
    await vi.waitFor(() => expect(calls.filter((c) => c.path.includes("&end=")).length).toBe(2)); // asked again from the new first candle
    const second = calls.filter((c) => c.path.includes("&end="))[1]!.path;
    expect(second).toContain(`end=${encodeURIComponent(at("15:25", "2026-10-15"))}`);
    await new Promise((r) => setTimeout(r, 50));
    expect(calls.filter((c) => c.path.includes("&end=")).length).toBe(2); // that page added nothing: done
  });

  it("the account's fills are marked and named in the readout", async () => {
    setup({
      [`GET /api/accounts/acc-a/charts/${ENC}/marks`]: {
        account_id: "acc-a",
        instrument_id: FUT,
        marks: [{ t: "2026-10-16T03:51:00+00:00", side: "BUY", qty: 75, price_paise: 2501000, client_order_id: "MT1", strategy: "manual" }],
      },
    });
    await vi.waitFor(() => expect(readout().textContent).toContain("▲ BUY 75 @ 25,010.00"));
  });

  it("a click picks a price for the linked ticket, snapped to the tick; unlinked, it says how", async () => {
    const { app } = setup();
    await vi.waitFor(() => expect(readout().textContent).toContain("25,015.00"));
    await screen.findByText("NIFTY26OCTFUT"); // the instrument (for its tick size)
    const picks: number[] = [];
    app.links.subscribePrice("pink", (p) => picks.push(p.pricePaise));
    fireEvent.click(chartImg(), { clientX: 100, clientY: 60 });
    expect(picks).toHaveLength(1);
    expect(picks[0]! % 10).toBe(0);
    expect(picks[0]!).toBeGreaterThan(2499000);
    expect(picks[0]!).toBeLessThan(2502000);
    expect(screen.getByText(/sent to the linked ticket/)).toBeTruthy();
    fireEvent.click(chartImg(), { clientX: 600, clientY: 60 }); // on the price axis: nothing
    fireEvent.click(chartImg(), { clientX: 100, clientY: 300 }); // in the volume pane: nothing
    expect(picks).toHaveLength(1);
  });

  it("an unlinked chart says how to trade from it", async () => {
    setup({}, { link: "none" });
    await vi.waitFor(() => expect(readout().textContent).toContain("25,015.00"));
    await screen.findByText("NIFTY26OCTFUT");
    fireEvent.click(chartImg(), { clientX: 100, clientY: 60 });
    expect(screen.getByText(/Link this chart to a ticket/)).toBeTruthy();
  });

  it("hovering reads the candle under the pointer; keys pan and zoom", async () => {
    setup();
    await vi.waitFor(() => expect(readout().textContent).toContain("25,015.00"));
    // 2 candles at 8 px, the newest ending at x = 568 (640 − the 72 px axis)
    fireEvent.mouseMove(chartImg(), { clientX: 555, clientY: 50 });
    expect(readout().textContent).toContain("09:15 O 25,000.00");
    fireEvent.mouseLeave(chartImg());
    expect(readout().textContent).toContain("09:20");
    fireEvent.keyDown(chartImg(), { key: "+" });
    fireEvent.keyDown(chartImg(), { key: "Home" });
    expect(chartImg().getAttribute("aria-label")).toContain("2 of 2 candles");
  });

  it("history the server couldn't get is said, and a missing contract asks for one", async () => {
    setup({ [`GET /api/charts/${ENC}/candles?interval=5m`]: { ...TODAY, note: "Kite history: TokenException" } });
    expect((await screen.findByText("history unavailable")).getAttribute("title")).toBe("Kite history: TokenException");
  });

  it("without a contract, a search to pick one", () => {
    setup({}, { instrumentId: undefined });
    expect(screen.getByText("Pick a contract for this chart.")).toBeTruthy();
  });
});
