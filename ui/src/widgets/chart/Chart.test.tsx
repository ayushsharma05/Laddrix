import { act, fireEvent, render, screen, within } from "@testing-library/react";
import type { CandlesAnswer, ChartSpec } from "../../api/types";
import { Rest } from "../../api/rest";
import { AppProvider, createApp } from "../../app/context";
import { fakeRest, fakeStream } from "../../test/fakes";
import { ME } from "../../test/fixtures";
import { widget } from "../../workspace/registry";
import { Chart, resetChartSpec, type ChartParams } from "./Chart";
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

  it("dragging pans back in time; the click that ends a drag picks nothing", async () => {
    const many: CandlesAnswer = { ...TODAY, candles: Array.from({ length: 120 }, (_, k) => candle(new Date(Date.parse(at("09:15")) + k * 300_000).toISOString(), 2500000 + k)) };
    const { app } = setup({ [`GET /api/charts/${ENC}/candles?interval=5m`]: many });
    await vi.waitFor(() => expect(chartImg().getAttribute("aria-label")).toContain("71 of 120 candles"));
    const picks: number[] = [];
    app.links.subscribePrice("pink", (p) => picks.push(p.pricePaise));
    fireEvent.mouseMove(chartImg(), { clientX: 500, clientY: 50 });
    const before = readout().textContent;
    fireEvent.mouseDown(chartImg(), { clientX: 100, clientY: 50 });
    // as a browser sends them: the move, the release and the click before React renders
    await act(async () => {
      fireEvent.mouseMove(chartImg(), { clientX: 180, clientY: 50 }); // 80 px = 10 candles back
      fireEvent.mouseUp(chartImg(), { clientX: 180, clientY: 50 });
      fireEvent.click(chartImg(), { clientX: 180, clientY: 50 });
      await new Promise((r) => setTimeout(r, 5)); // the drag has ended before the pan is drawn
    });
    expect(picks).toEqual([]);
    fireEvent.mouseMove(chartImg(), { clientX: 500, clientY: 50 });
    expect(readout().textContent).not.toBe(before);
    expect(readout().textContent).toContain("O 25,001.01"); // candle 101: ten older than the 111th under the pointer before
    await screen.findByText("NIFTY26OCTFUT");
    fireEvent.click(chartImg(), { clientX: 300, clientY: 60 }); // a plain click after the drag picks
    expect(picks).toHaveLength(1);
  });

  it("an older page asked for one interval is dropped once another interval is shown", async () => {
    resetChartSpec();
    let release: (r: Response) => void = () => {};
    const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
    const rest = new Rest(async (path) => {
      if (path === "/api/charts/spec") return json(SPEC);
      if (path.startsWith(`/api/instruments/`)) return json(NIFTY);
      if (path.includes("/marks")) return json({ marks: [] });
      if (path.includes("interval=5m&start=")) return new Promise<Response>((r) => (release = r)); // held back
      if (path.includes("interval=1m")) return json({ ...TODAY, interval: "1m", candles: [candle(at("09:21"), 2600000)] });
      return json(TODAY);
    });
    const { stream, socket } = fakeStream();
    const app = createApp({ rest, stream });
    app.store.setMe(ME);
    app.store.selectAccount("acc-a");
    stream.start();
    socket().open();
    const props = { params: { link: "pink" as const, instrumentId: FUT, interval: "5m" }, setParams: vi.fn(), setInstrument: vi.fn(), publish: vi.fn(), open: vi.fn() };
    const view = render(
      <AppProvider app={app}>
        <Chart {...props} />
      </AppProvider>,
    );
    await vi.waitFor(() => expect(chartImg().getAttribute("aria-label")).toContain("5m chart: 2 of 2"));
    view.rerender(
      <AppProvider app={app}>
        <Chart {...props} params={{ ...props.params, interval: "1m" }} />
      </AppProvider>,
    );
    await vi.waitFor(() => expect(chartImg().getAttribute("aria-label")).toContain("1m chart: 1 of 1"));
    await act(async () => release(json({ ...TODAY, candles: [candle(at("09:10"), 1), candle(at("09:05"), 2)] })));
    expect(chartImg().getAttribute("aria-label")).toContain("1m chart: 1 of 1"); // the 5m page didn't join
  });

  it("older pages wait for the first load: never two history reads at once", async () => {
    resetChartSpec();
    let release: (r: Response) => void = () => {};
    const asked: string[] = [];
    const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
    const rest = new Rest(async (path) => {
      asked.push(path);
      if (path === "/api/charts/spec") return json(SPEC);
      if (path.startsWith(`/api/instruments/`)) return json(NIFTY);
      if (path.includes("/marks")) return json({ marks: [] });
      if (path.endsWith("candles?interval=5m")) return new Promise<Response>((r) => (release = r)); // slow
      return json({ ...TODAY, candles: [] });
    });
    const { stream, socket } = fakeStream();
    const app = createApp({ rest, stream });
    app.store.setMe(ME);
    app.store.selectAccount("acc-a");
    stream.start();
    socket().open();
    render(
      <AppProvider app={app}>
        <Chart params={{ link: "pink", instrumentId: FUT, interval: "5m" }} setParams={vi.fn()} setInstrument={vi.fn()} publish={vi.fn()} open={vi.fn()} />
      </AppProvider>,
    );
    await vi.waitFor(() => expect(socket().sent).toContainEqual({ op: "candles", instrument_id: FUT, interval: "5m" }));
    act(() => socket().receive({ type: "candle", instrument_id: FUT, interval: "5m", candle: candle(at("09:25"), 2500000) }));
    expect(await screen.findByText("loading history…")).toBeTruthy();
    await new Promise((r) => setTimeout(r, 20));
    expect(asked.filter((p) => p.includes("&end="))).toEqual([]); // the first load hasn't answered
    await act(async () => release(json(TODAY)));
    await vi.waitFor(() => expect(asked.filter((p) => p.includes("&end=")).length).toBe(1));
    expect(chartImg().getAttribute("aria-label")).toContain("3 of 3 candles");
    expect(screen.queryByText("loading history…")).toBeNull();
  });

  it("history the server couldn't get is said, and a missing contract asks for one", async () => {
    setup({ [`GET /api/charts/${ENC}/candles?interval=5m`]: { ...TODAY, note: "Kite history: TokenException" } });
    expect((await screen.findByText("history unavailable")).getAttribute("title")).toBe("Kite history: TokenException");
  });

  it("a spread's chart says it is a mark from its legs, and how many gaps (#429)", async () => {
    const label = "spread mark from leg prices, not traded prices; gaps where a leg had no data";
    setup({ [`GET /api/charts/${ENC}/candles?interval=5m`]: { ...TODAY, kind: "spread mark", label, gaps: 2 } });
    const note = await screen.findByRole("note");
    expect(note.textContent).toBe("spread mark, not traded prices · 2 gaps");
    expect(note.getAttribute("title")).toBe(label);
  });

  it("a spread's chart is titled with the spread's name, not its id (#429)", async () => {
    const SP = "SPREAD:sp1";
    const enc = encodeURIComponent(SP);
    const { setParams } = setup(
      {
        [`GET /api/instruments/${enc}`]: { ...NIFTY, id: SP, exchange: "SPREAD", symbol: "NIFTY Oct/Nov", kind: "SPREAD" },
        [`GET /api/charts/${enc}/candles?interval=5m`]: { ...TODAY, instrument_id: SP, kind: "spread mark", label: "spread mark", gaps: 0 },
        [`GET /api/charts/${enc}/candles`]: { ...OLDER, candles: [] },
        [`GET /api/accounts/acc-a/charts/${enc}/marks`]: { account_id: "acc-a", instrument_id: SP, marks: [] },
      },
      { instrumentId: SP, label: "old name" }, // renamed since the window was saved
    );
    await vi.waitFor(() => expect(setParams).toHaveBeenCalledWith({ label: "NIFTY Oct/Nov" }));
    const title = widget("chart")!.tabTitle!;
    expect(title({ link: "none", instrumentId: SP, interval: "5m", label: "NIFTY Oct/Nov" } as ChartParams)).toBe("NIFTY Oct/Nov 5m");
    expect(title({ link: "none", instrumentId: FUT, interval: "5m", label: "stale" } as ChartParams)).toBe("NIFTY26OCTFUT 5m"); // only a spread's
  });

  it("a contract's chart keeps its own title: no label is set", async () => {
    const { setParams } = setup({}, { label: "stale" }); // only a spread's name is kept
    await vi.waitFor(() => expect(readout().textContent).toContain("C 25,015.00"));
    expect(setParams.mock.calls.filter(([p]) => "label" in (p as object))).toEqual([]);
  });

  it("without a contract, a search to pick one", () => {
    setup({}, { instrumentId: undefined });
    expect(screen.getByText("Pick a contract for this chart.")).toBeTruthy();
  });
});
