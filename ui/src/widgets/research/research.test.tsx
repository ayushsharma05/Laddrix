import { fireEvent, render, screen, within } from "@testing-library/react";
import type { BacktestDay, DayReport } from "../../api/types";
import { AppProvider, createApp } from "../../app/context";
import { fakeRest, fakeStream } from "../../test/fakes";
import { ME } from "../../test/fixtures";
import { Backtests, POLL_MS } from "./Backtests";
import { Recordings } from "./Recordings";
import { FORM, FUT, run, summary } from "./testdata";

const NIFTY = { id: FUT, exchange: "NFO", symbol: "NIFTY26OCTFUT", kind: "FUT", tick_paise: 10, lot_size: 75, expiry: "2026-10-27", strike_paise: null, underlying: "NIFTY", tradable: true, ltp_paise: 2501000 };

const REPORT: DayReport = {
  account_id: "backtest",
  day: "2026-10-15",
  generated_at: "2026-10-15T10:30:00+00:00",
  final: true,
  priced: true,
  notes: [],
  schedule: "standard",
  totals: { realised_paise: 0, unrealised_paise: 50000, gross_paise: 50000, charges_paise: 10000, net_paise: 40000, turnover_paise: 375000000, trades: 2 },
  charge_lines: [],
  positions: [],
  strategies: [],
  trades: [
    { ts: "2026-10-15T03:45:00+00:00", client_order_id: "BT1", instrument_id: FUT, side: "BUY", product: "NRML", qty: 75, price_paise: 2500010, value_paise: 187500750, strategy: "algo:A0001" },
    { ts: "2026-10-15T03:46:00+00:00", client_order_id: "BT2", instrument_id: FUT, side: "BUY", product: "NRML", qty: 75, price_paise: 2500210, value_paise: 187515750, strategy: "algo:A0001" },
  ],
  rejects: [{ ts: "2026-10-15T03:47:00+00:00", detail: "BT3: position limit" }],
  alerts: [],
};
const DAILY: BacktestDay[] = [
  { day: "2026-10-14", skipped: "nothing recorded" },
  {
    ...REPORT,
    equity_curve: [
      ["2026-10-15T03:45:00+00:00", 0],
      ["2026-10-15T03:46:00+00:00", 20000],
      ["2026-10-15T03:50:00+00:00", 50000],
    ],
    algo_started: true,
  },
];

function mount(node: React.ReactNode, routes: Record<string, unknown>) {
  const server = fakeRest(routes);
  const { stream, socket } = fakeStream();
  const app = createApp({ rest: server.rest, stream });
  app.store.setMe(ME);
  app.store.selectAccount("acc-a");
  stream.start();
  socket().open();
  render(<AppProvider app={app}>{node}</AppProvider>);
  return server;
}

function props(params: Record<string, unknown> = {}) {
  return { params: { link: "pink" as const, ...params }, setParams: vi.fn(), setInstrument: vi.fn(), publish: vi.fn(), open: vi.fn() };
}

const gets = (calls: { method: string; path: string }[], path: string) => calls.filter((c) => c.method === "GET" && c.path === path).length;

describe("Recordings", () => {
  it("lists the kept days; a day opens to its instruments, busiest first", async () => {
    const p = props();
    mount(<Recordings {...p} />, {
      "GET /api/research/recordings": {
        keep_days: 30,
        days: [
          { day: "2026-10-16", ticks: 123456, bytes: 10 * 1024 * 1024, segments: { NFO: 9 * 1024 * 1024, NSE: 1024 * 1024 }, instruments: 2 },
          { day: "2026-10-15", ticks: 500, bytes: 2048, segments: { NFO: 2048 }, instruments: 1 },
        ],
      },
      "GET /api/research/recordings/2026-10-16": { day: "2026-10-16", ticks: 123456, bytes: 1, segments: {}, instruments: { "NSE:RELIANCE": 3210, [FUT]: 120246 } },
    });
    const grid = await screen.findByRole("grid", { name: "Recorded days" });
    await within(grid).findByText("2026-10-16");
    const rows = within(grid).getAllByRole("row");
    expect(rows[0]!.textContent).toContain("1,23,456");
    expect(rows[0]!.textContent).toContain("10 MB");
    expect(rows[0]!.textContent).toContain("NFO 9.0 MB · NSE 1.0 MB");
    expect(rows[1]!.textContent).toContain("2.0 KB");
    expect(screen.getByText("Kept 30 days")).toBeTruthy();
    fireEvent.click(rows[0]!);
    const list = await screen.findByRole("list", { name: "Instruments on 2026-10-16" });
    const items = within(list).getAllByRole("listitem");
    expect(items.map((i) => i.textContent)).toEqual(["NIFTY26OCTFUT 1,20,246", "RELIANCE 3,210"]);
    fireEvent.click(items[1]!);
    expect(p.publish).toHaveBeenCalledWith("NSE:RELIANCE");
    fireEvent.click(rows[0]!); // closes again
    expect(screen.queryByRole("list", { name: "Instruments on 2026-10-16" })).toBeNull();
  });

  it("an admin deletes a day after confirming; the replay command can be copied", async () => {
    let days = [
      { day: "2026-10-16", ticks: 10, bytes: 2048, segments: {}, instruments: 1, deletable: false, replay: "replay 2026-10-16" },
      { day: "2026-10-15", ticks: 5, bytes: 1024, segments: {}, instruments: 1, deletable: true, replay: "replay 2026-10-15" },
    ];
    const writeText = vi.fn(() => Promise.resolve());
    Object.assign(navigator, { clipboard: { writeText } });
    const server = mount(<Recordings {...props()} />, {
      "GET /api/research/recordings": () => ({ status: 200, body: { keep_days: 30, days } }),
      "GET /api/research/recordings/2026-10-16": { day: "2026-10-16", ticks: 10, bytes: 2048, segments: {}, instruments: { [FUT]: 10 } },
      "GET /api/research/recordings/2026-10-15": { day: "2026-10-15", ticks: 5, bytes: 1024, segments: {}, instruments: { [FUT]: 5 } },
      "DELETE /api/research/recordings/2026-10-15": () => {
        days = days.slice(0, 1);
        return { status: 200, body: { day: "2026-10-15", deleted: true } };
      },
    });
    const grid = await screen.findByRole("grid", { name: "Recorded days" });
    fireEvent.click(await within(grid).findByText("2026-10-16"));
    const today = await screen.findByLabelText("Actions for 2026-10-16");
    expect(within(today).queryByRole("button", { name: "Delete…" })).toBeNull(); // still being written
    expect(within(today).getByText("replay 2026-10-16")).toBeTruthy();
    fireEvent.click(within(today).getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith("replay 2026-10-16");
    expect(await within(today).findByRole("button", { name: "Copied" })).toBeTruthy();
    fireEvent.click(within(grid).getByText("2026-10-15"));
    const old = await screen.findByLabelText("Actions for 2026-10-15");
    fireEvent.click(within(old).getByRole("button", { name: "Delete…" }));
    expect(server.calls.some((c) => c.method === "DELETE")).toBe(false); // one click only arms it
    fireEvent.click(within(old).getByRole("button", { name: "Confirm: delete 2026-10-15 (1.0 KB)" }));
    await vi.waitFor(() => expect(within(grid).queryByText("2026-10-15")).toBeNull());
    expect(server.calls.filter((c) => c.method === "DELETE").map((c) => c.path)).toEqual(["/api/research/recordings/2026-10-15"]);
  });

  it("a refused delete says why", async () => {
    mount(<Recordings {...props()} />, {
      "GET /api/research/recordings": { keep_days: 30, days: [{ day: "2026-10-15", ticks: 5, bytes: 1024, segments: {}, instruments: 1, deletable: true }] },
      "GET /api/research/recordings/2026-10-15": { day: "2026-10-15", ticks: 5, bytes: 1024, segments: {}, instruments: {} },
      "DELETE /api/research/recordings/2026-10-15": () => ({ status: 409, body: { detail: "2026-10-15 is today: it is still being recorded" } }),
    });
    fireEvent.click(await screen.findByText("2026-10-15"));
    const actions = await screen.findByLabelText("Actions for 2026-10-15");
    expect(within(actions).queryByText(/Replay:/)).toBeNull(); // this server can't replay its days
    fireEvent.click(within(actions).getByRole("button", { name: "Delete…" }));
    fireEvent.click(within(actions).getByRole("button", { name: /^Confirm: delete/ }));
    expect((await within(actions).findByRole("alert")).textContent).toBe("2026-10-15 is today: it is still being recorded");
    expect(within(actions).getByRole("button", { name: "Delete…" })).toBeTruthy(); // disarmed again
  });

  it("says so when this server doesn't record, or nothing is recorded yet", async () => {
    mount(<Recordings {...props()} />, {});
    expect(await screen.findByText(/Not recording: not found/)).toBeTruthy();
  });

  it("an empty recording", async () => {
    mount(<Recordings {...props()} />, { "GET /api/research/recordings": { keep_days: 0, days: [] } });
    expect(await screen.findByText(/Nothing recorded yet/)).toBeTruthy();
    expect(screen.getByText("Kept until deleted")).toBeTruthy();
  });
});

describe("Backtests", () => {
  it("follows a run in progress each second and stops asking once every run has finished", async () => {
    let n = 0;
    const server = mount(<Backtests {...props()} />, {
      "GET /api/research/backtests": () => {
        n += 1;
        if (n === 1) return { status: 200, body: [run("BT2", { state: "queued", progress: 0, summary: undefined }), run("BT1")] };
        if (n === 2) return { status: 200, body: [run("BT2", { state: "running", progress: 1, summary: undefined }), run("BT1")] };
        return { status: 200, body: [run("BT2"), run("BT1")] };
      },
    });
    const grid = await screen.findByRole("grid", { name: "Backtest runs" });
    await within(grid).findByText("Waiting for the run before it");
    expect(within(grid).getAllByRole("row")[1]!.textContent).toContain("+₹400.00"); // the finished one
    await within(grid).findByText("Running: 1 of 2 days", {}, { timeout: POLL_MS * 2 });
    await vi.waitFor(() => expect(within(grid).getAllByText("Done")).toHaveLength(2), { timeout: POLL_MS * 2 });
    await new Promise((r) => setTimeout(r, POLL_MS * 1.5));
    expect(gets(server.calls, "/api/research/backtests")).toBe(3); // stopped
  }, 10_000);

  it("a run's results: totals, its curve, its days (CSV/PDF), and a day's own curve and trades", async () => {
    const p = props();
    mount(<Backtests {...p} />, {
      "GET /api/research/backtests": [run("BT1")],
      "GET /api/research/backtests/BT1": run("BT1", { daily: DAILY }),
    });
    fireEvent.click(await screen.findByText("SLICER BUY 150 NIFTY26OCTFUT"));
    const detail = await screen.findByRole("region", { name: "Backtest BT1" });
    const totals = await within(detail).findByLabelText("Run totals");
    expect(totals.textContent).toContain("Net +₹400.00");
    expect(totals.textContent).toContain("Max drawdown ₹50.00");
    expect(totals.textContent).toContain("Winning days 1 of 2 (50.0%)");
    expect(within(detail).getByText(/capital ₹10,00,000.00 · slices 2/)).toBeTruthy();
    expect(within(detail).getByRole("img", { name: /Cumulative net by day: Cumulative net ends at \+₹400.00/ })).toBeTruthy();
    const chart = within(detail).getByRole("img", { name: /Cumulative net by day/ });
    fireEvent.mouseMove(chart, { clientX: 0 }); // jsdom has no layout: the first day
    expect(within(detail).getByRole("status").textContent).toBe("2026-10-14−₹50.00");
    fireEvent.mouseLeave(chart);
    expect(within(detail).queryByRole("status")).toBeNull();
    const days = within(detail).getByRole("grid", { name: "Backtest days" });
    const rows = within(days).getAllByRole("row");
    expect(rows[0]!.textContent).toContain("skipped: nothing recorded");
    expect(within(rows[0]!).queryByText("CSV")).toBeNull(); // nothing to download
    expect(within(rows[1]!).getByText("CSV").getAttribute("href")).toBe("/api/research/backtests/BT1/days/2026-10-15?format=csv");
    expect(within(rows[1]!).getByText("PDF").getAttribute("href")).toBe("/api/research/backtests/BT1/days/2026-10-15?format=pdf");
    fireEvent.click(rows[1]!);
    const day = within(detail).getByRole("region", { name: "Day 2026-10-15" });
    expect(within(day).getByRole("img", { name: /P&L before charges.*ends at \+₹500.00/ })).toBeTruthy();
    const trades = within(day).getAllByRole("row");
    expect(trades).toHaveLength(2);
    expect(trades[0]!.textContent).toContain("25,000.10");
    fireEvent.click(trades[0]!);
    expect(p.publish).toHaveBeenCalledWith(FUT);
    expect(within(day).getByText(/rejected: BT3: position limit/)).toBeTruthy();
    fireEvent.click(rows[0]!); // a skipped day opens nothing
    expect(within(detail).queryByRole("region", { name: "Day 2026-10-14" })).toBeNull();
  });

  it("the picked run is read again as it moves on, so its results appear when it finishes", async () => {
    let n = 0;
    const running = run("BT1", { state: "running", progress: 1, summary: undefined });
    mount(<Backtests {...props()} />, {
      "GET /api/research/backtests": () => {
        n += 1;
        return { status: 200, body: [n < 2 ? running : run("BT1")] };
      },
      "GET /api/research/backtests/BT1": () => ({ status: 200, body: n < 2 ? running : run("BT1", { daily: DAILY }) }),
    });
    fireEvent.click(await screen.findByText("SLICER BUY 150 NIFTY26OCTFUT"));
    const detail = await screen.findByRole("region", { name: "Backtest BT1" });
    expect(within(detail).queryByLabelText("Run totals")).toBeNull();
    expect((await within(detail).findByLabelText("Run totals", {}, { timeout: POLL_MS * 2 })).textContent).toContain("Net +₹400.00");
  }, 10_000);

  it("a failed run says why", async () => {
    mount(<Backtests {...props()} />, {
      "GET /api/research/backtests": [run("BT1", { state: "failed", error: "2026-10-15: the algo couldn't start: slices must be at least 1", summary: undefined })],
      "GET /api/research/backtests/BT1": run("BT1", { state: "failed", error: "2026-10-15: the algo couldn't start: slices must be at least 1", summary: undefined }),
    });
    fireEvent.click(await screen.findByText("SLICER BUY 150 NIFTY26OCTFUT"));
    const detail = await screen.findByRole("region", { name: "Backtest BT1" });
    expect(within(detail).getByText("2026-10-15: the algo couldn't start: slices must be at least 1")).toBeTruthy();
    expect(within(detail).queryByLabelText("Run totals")).toBeNull();
  });

  it("a waiting or running run can be cancelled; a finished one can't", async () => {
    let cancelled = false;
    const going = () => run("BT1", cancelled ? { state: "cancelled", error: "cancelled", summary: undefined } : { state: "running", progress: 0, summary: undefined });
    const server = mount(<Backtests {...props()} />, {
      "GET /api/research/backtests": () => ({ status: 200, body: [going()] }),
      "GET /api/research/backtests/BT1": () => ({ status: 200, body: going() }),
      "POST /api/research/backtests/BT1/cancel": () => {
        cancelled = true;
        return { status: 200, body: going() };
      },
    });
    fireEvent.click(await screen.findByText("SLICER BUY 150 NIFTY26OCTFUT"));
    const detail = await screen.findByRole("region", { name: "Backtest BT1" });
    fireEvent.click(within(detail).getByRole("button", { name: "Cancel this run" }));
    const grid = screen.getByRole("grid", { name: "Backtest runs" });
    await within(grid).findByText("Cancelled");
    expect(server.calls.filter((c) => c.method === "POST").map((c) => c.path)).toEqual(["/api/research/backtests/BT1/cancel"]);
    await vi.waitFor(() => expect(within(detail).queryByRole("button", { name: "Cancel this run" })).toBeNull());
  });

  it("compare: two to four finished runs side by side, each keeping its colour", async () => {
    const b = run("BT2", { spec: { ...run("BT2").spec, name: "patient" }, summary: summary({ net_paise: -2000, equity_curve: [["2026-10-16", -2000]] }) });
    mount(<Backtests {...props({ mode: "compare" })} />, {
      "GET /api/research/backtests": [run("BT3", { state: "running", summary: undefined, spec: { ...run("BT3").spec, name: "slow" } }), b, run("BT1")],
    });
    expect(await screen.findByText(/Tick two to four finished runs/)).toBeTruthy();
    const box = (name: string) => screen.getByRole("checkbox", { name: `Compare ${name}` }) as HTMLInputElement;
    expect(box("SLICER BUY 150 NIFTY26OCTFUT").disabled).toBe(false);
    expect(box("slow").disabled).toBe(true); // still running
    fireEvent.click(box("patient"));
    fireEvent.click(box("SLICER BUY 150 NIFTY26OCTFUT"));
    const region = screen.getByRole("region", { name: "Compare runs" });
    const table = within(region).getByRole("table");
    const heads = within(table).getAllByRole("columnheader").map((h) => h.textContent);
    expect(heads).toEqual(["", "patient", "SLICER BUY 150 NIFTY26OCTFUT"]);
    const net = within(table).getByRole("row", { name: /^Net/ });
    expect(net.textContent).toBe("Net−₹20.00+₹400.00");
    expect(within(table).getByRole("row", { name: /^Days \d/ }).textContent).toContain("2026-10-14 → 2026-10-15");
    expect(within(region).getByLabelText("Legend").textContent).toBe("patientSLICER BUY 150 NIFTY26OCTFUT");
    expect(region.querySelectorAll("path.chart-line.slot-0, path.chart-line.slot-1")).toHaveLength(2);
    fireEvent.click(box("patient")); // patient goes; the other keeps slot 1
    expect(screen.getByText(/Tick two to four finished runs/)).toBeTruthy();
    fireEvent.click(box("patient"));
    expect(screen.getByRole("region", { name: "Compare runs" }).querySelector("path.chart-line.slot-1")).toBeTruthy();
  });

  it("says so when backtests aren't set up on this server", async () => {
    mount(<Backtests {...props()} />, {});
    expect(await screen.findByText(/Backtests aren't available: not found/)).toBeTruthy();
  });
});

describe("New backtest", () => {
  function setup(routes: Record<string, unknown> = {}, params: Record<string, unknown> = {}) {
    const p = props({ mode: "new", instrumentId: FUT, ...params });
    const server = mount(<Backtests {...p} />, {
      "GET /api/research/backtests": [],
      "GET /api/research/backtests/form": (_: unknown) => ({ status: 200, body: { ...FORM, max_days: 250, candle_days: ["2026-10-13"] } }),
      [`GET /api/instruments/${encodeURIComponent(FUT)}`]: NIFTY,
      "POST /api/research/backtests": (body: unknown) => ({ status: 201, body: run("BT9", { state: "queued", summary: undefined, spec: { ...run("BT9").spec, ...(body as object) } }) }),
      ...routes,
    });
    return { ...server, ...p };
  }
  const posts = (calls: { method: string; path: string; body: unknown }[]) => calls.filter((c) => c.method === "POST");

  it("the form is drawn from the server: algos, settings, products, days and defaults; a run starts", async () => {
    const { calls, setParams } = setup();
    const form = await screen.findByRole("form", { name: "Start a backtest" });
    expect((within(form).getByRole("textbox", { name: "Slices" }) as HTMLInputElement).value).toBe("10");
    expect((within(form).getByRole("textbox", { name: "Start time" }) as HTMLInputElement).value).toBe("09:15");
    expect((within(form).getByRole("textbox", { name: "Capital" }) as HTMLInputElement).value).toBe("1000000.00");
    expect((within(form).getByRole("combobox", { name: "Product" }) as HTMLSelectElement).value).toBe("NRML");
    expect(calls.some((c) => c.path === `/api/research/backtests/form?instrument_id=${encodeURIComponent(FUT)}`)).toBe(true);
    const days = within(form).getByRole("group", { name: "Days to run" });
    expect(within(days).getAllByRole("checkbox").map((c) => c.parentElement!.textContent)).toEqual(FORM.days);
    fireEvent.click(within(days).getByRole("checkbox", { name: "2026-10-16" }));
    fireEvent.click(within(days).getByRole("checkbox", { name: "2026-10-14" }));
    fireEvent.change(within(form).getByRole("textbox", { name: "Slices" }), { target: { value: "4" } });
    fireEvent.change(within(form).getByRole("combobox", { name: "Price" }), { target: { value: "cross" } });
    fireEvent.change(within(form).getByRole("textbox", { name: "Lots" }), { target: { value: "2" } });
    fireEvent.click(within(form).getByRole("button", { name: "SELL" }));
    fireEvent.change(within(form).getByRole("textbox", { name: "Run name" }), { target: { value: "try 1" } });
    await within(form).findByText("= 150");
    fireEvent.click(within(form).getByRole("button", { name: "Backtest Slicer SELL 150 NIFTY26OCTFUT over 2 days" }));
    await vi.waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]!.body).toEqual({
      days: ["2026-10-14", "2026-10-16"],
      algo: "SLICER",
      instrument_id: FUT,
      side: "SELL",
      qty: 150,
      params: { slices: 4, mode: "cross" },
      product: "NRML",
      start: "09:15",
      capital_paise: 100000000,
      name: "try 1",
      candles: false,
    });
    await vi.waitFor(() => expect(setParams).toHaveBeenCalledWith({ mode: "runs" }));
  });

  it("switching algo redraws its settings; All and None pick days; problems are listed and nothing is sent", async () => {
    const { calls } = setup();
    const form = await screen.findByRole("form", { name: "Start a backtest" });
    fireEvent.change(within(form).getByRole("combobox", { name: "Algo" }), { target: { value: "TWAP" } });
    expect((within(form).getByRole("textbox", { name: "Minutes" }) as HTMLInputElement).value).toBe("30");
    expect(within(form).queryByRole("textbox", { name: "Slices" })).toBeNull();
    fireEvent.click(within(form).getByRole("button", { name: "All" }));
    expect(within(form).getByText("3 picked")).toBeTruthy();
    fireEvent.click(within(form).getByRole("button", { name: "None" }));
    fireEvent.change(within(form).getByRole("textbox", { name: "Start time" }), { target: { value: "9:15" } });
    fireEvent.click(within(form).getByRole("button", { name: /^Backtest TWAP/ }));
    const alert = await within(form).findByRole("alert");
    expect(alert.textContent).toBe("Pick at least one dayStart must be a time like 09:20");
    expect(posts(calls)).toHaveLength(0);
  });

  it("the server's refusal is shown in the form", async () => {
    setup({ "POST /api/research/backtests": () => ({ status: 422, body: { detail: "unknown instrument NFO:X" } }) });
    const form = await screen.findByRole("form", { name: "Start a backtest" });
    fireEvent.click(within(within(form).getByRole("group", { name: "Days to run" })).getByRole("checkbox", { name: "2026-10-16" }));
    await within(form).findByText("= 75");
    fireEvent.click(within(form).getByRole("button", { name: /^Backtest Slicer/ }));
    expect((await within(form).findByRole("alert")).textContent).toBe("unknown instrument NFO:X");
  });

  it("from candles: the contract's candle days; days not offered any more are dropped", async () => {
    const { calls } = setup({
      "POST /api/research/backtests": (body: unknown) => ({ status: 201, body: run("BT9", { spec: { ...run("BT9").spec, ...(body as object) } }) }),
    });
    const form = await screen.findByRole("form", { name: "Start a backtest" });
    const days = () => within(form).getByRole("group", { name: "Days to run" });
    fireEvent.click(within(days()).getByRole("checkbox", { name: "2026-10-16" }));
    fireEvent.click(within(form).getByRole("checkbox", { name: "from candles" }));
    expect(within(days()).getAllByRole("checkbox").map((c) => c.parentElement!.textContent)).toEqual(["2026-10-13"]);
    expect(within(form).getByText("0 picked")).toBeTruthy(); // the recorded day was dropped
    fireEvent.click(within(days()).getByRole("checkbox", { name: "2026-10-13" }));
    await within(form).findByText("= 75");
    fireEvent.click(within(form).getByRole("button", { name: /^Backtest Slicer/ }));
    await vi.waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]!.body).toMatchObject({ days: ["2026-10-13"], candles: true });
  });
});
