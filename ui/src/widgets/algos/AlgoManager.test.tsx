import { act, fireEvent, render, screen, within } from "@testing-library/react";
import type { AlgoSpec, AlgoView } from "../../api/types";
import { AppProvider, createApp } from "../../app/context";
import { fakeRest, fakeStream } from "../../test/fakes";
import { algo, ME, order, orderSpec } from "../../test/fixtures";
import { AlgoManager } from "./AlgoManager";

const FUT = "NFO:NIFTY26OCTFUT";
const NOV = "NFO:NIFTY26NOVFUT";
const NIFTY = { id: FUT, exchange: "NFO", symbol: "NIFTY26OCTFUT", kind: "FUT", tick_paise: 10, lot_size: 75, expiry: "2026-10-27", strike_paise: null, underlying: "NIFTY", tradable: true, ltp_paise: 2501000 };
const NIFTY_NOV = { ...NIFTY, id: NOV, symbol: "NIFTY26NOVFUT", expiry: "2026-11-24" };

const SPECS: AlgoSpec[] = [
  {
    code: "SLICER",
    name: "Slicer",
    help: "Works a big order as equal slices on a clock",
    params: [
      { name: "slices", label: "Slices", kind: "count", help: "", required: true, default: 10, min: 1, max: 100, choices: [] },
      { name: "mode", label: "Price", kind: "choice", help: "", required: true, default: "join", min: null, max: null, choices: ["join", "cross"] },
      { name: "limit_paise", label: "Limit", kind: "price", help: "", required: false, default: null, min: null, max: null, choices: [] },
    ],
  },
  {
    code: "SPREAD",
    name: "Spreader",
    help: "Trades leg A against leg B",
    params: [
      { name: "leg2", label: "Leg B", kind: "instrument", help: "", required: true, default: null, min: null, max: null, choices: [] },
      { name: "level_paise", label: "Spread (A − B)", kind: "price_offset", help: "", required: true, default: null, min: null, max: null, choices: [] },
    ],
  },
];

function setup(routes: Record<string, unknown> = {}, params: Record<string, unknown> = {}) {
  const server = fakeRest({
    "GET /api/accounts/acc-a/algo-specs": SPECS,
    [`GET /api/instruments/${encodeURIComponent(FUT)}`]: NIFTY,
    [`GET /api/accounts/acc-a/instruments/${encodeURIComponent(FUT)}/order-spec`]: orderSpec(FUT),
    "GET /api/instruments": [NIFTY_NOV],
    "POST /api/accounts/acc-a/algos": (body: unknown) => {
      const b = body as Record<string, unknown>;
      return { status: 201, body: algo("A0009", { code: String(b.code), qty: Number(b.qty), side: b.side as AlgoView["side"], params: b.params as Record<string, unknown> }) };
    },
    ...routes,
  });
  const { stream, socket } = fakeStream();
  const app = createApp({ rest: server.rest, stream });
  app.store.setMe(ME);
  app.store.selectAccount("acc-a");
  stream.start();
  socket().open();
  const props = { params: { link: "pink" as const, ...params }, setParams: vi.fn(), setInstrument: vi.fn(), publish: vi.fn(), open: vi.fn() };
  render(
    <AppProvider app={app}>
      <AlgoManager {...props} />
    </AppProvider>,
  );
  return { ...server, ...props, app, socket };
}

const rows = () => within(screen.getByRole("grid", { name: "Algos" })).queryAllByRole("row");
const posts = (calls: { method: string; path: string; body: unknown }[]) => calls.filter((c) => c.method === "POST");

describe("Algo manager", () => {
  it("lists algos live from the stream, running first, with their state", () => {
    const { app, socket } = setup();
    act(() => app.store.applyAlgos("acc-a", [algo("A0001", { state: "DONE", filled_qty: 750, avg_price_paise: 2501000 }), algo("A0002", { status: "slice 3 of 10" })]));
    expect(rows().map((r) => r.textContent)).toEqual([expect.stringContaining("A0002"), expect.stringContaining("A0001")]);
    expect(rows()[0]!.textContent).toContain("slice 3 of 10");
    expect(rows()[1]!.textContent).toContain("25,010.00");
    act(() =>
      socket().receive({ type: "algo", account_id: "acc-a", kind: "UPDATED", detail: "", algo: algo("A0002", { filled_qty: 150, status: "slice 4 of 10", updated_at: "2026-10-14T04:41:00+00:00" }) }),
    );
    expect(rows()[0]!.textContent).toContain("slice 4 of 10");
    // a late, older message never rolls the row back
    act(() => socket().receive({ type: "algo", account_id: "acc-a", kind: "UPDATED", detail: "", algo: algo("A0002", { status: "slice 3 of 10" }) }));
    expect(rows()[0]!.textContent).toContain("slice 4 of 10");
  });

  it("an algo alert reaches Messages; routine updates don't", () => {
    const { app, socket } = setup();
    const before = app.store.getState().messages.length;
    act(() => socket().receive({ type: "algo", account_id: "acc-a", kind: "UPDATED", detail: "", algo: algo("A0002") }));
    expect(app.store.getState().messages.length).toBe(before);
    act(() =>
      socket().receive({ type: "algo", account_id: "acc-a", kind: "ALERT", detail: "UNHEDGED: 75 on NFO:NIFTY26OCTFUT", algo: algo("A0002", { updated_at: "2026-10-14T04:42:00+00:00" }) }),
    );
    expect(app.store.getState().messages.some((m) => m.level === "ERROR" && m.text.includes("UNHEDGED: 75"))).toBe(true);
  });

  it("pause, resume and stop act on the selected algo; Stop all stops every running one", async () => {
    const routes = Object.fromEntries(
      ["A0001", "A0002"].flatMap((id) => ["pause", "resume", "stop"].map((a) => [`POST /api/accounts/acc-a/algos/${id}/${a}`, algo(id, { state: a === "pause" ? "PAUSED" : a === "stop" ? "STOPPED" : "WORKING", updated_at: "2026-10-14T05:00:00+00:00" })])),
    );
    const { app, calls, publish } = setup(routes);
    act(() => app.store.applyAlgos("acc-a", [algo("A0001"), algo("A0002", { state: "PAUSED" }), algo("A0003", { state: "DONE" })]));
    const button = (name: string) => screen.getByRole("button", { name }) as HTMLButtonElement;
    expect(button("Pause").disabled).toBe(true); // nothing selected
    fireEvent.click(rows().find((r) => r.title.startsWith("A0001"))!);
    expect(publish).toHaveBeenCalledWith(FUT);
    expect(button("Resume").disabled).toBe(true);
    fireEvent.click(button("Pause"));
    await vi.waitFor(() => expect(posts(calls).map((c) => c.path)).toEqual(["/api/accounts/acc-a/algos/A0001/pause"]));
    await vi.waitFor(() => expect(app.store.getState().books["acc-a"]!.algos.A0001!.state).toBe("PAUSED"));
    fireEvent.click(button("Stop all (2)"));
    await vi.waitFor(() => expect(posts(calls)).toHaveLength(3));
    expect(posts(calls).slice(1).map((c) => c.path).sort()).toEqual(["/api/accounts/acc-a/algos/A0001/stop", "/api/accounts/acc-a/algos/A0002/stop"]);
  });

  it("stop still works while trading is stopped; pause doesn't", async () => {
    const { app, calls } = setup({ "POST /api/accounts/acc-a/algos/A0001/stop": algo("A0001", { state: "STOPPED", updated_at: "2026-10-14T05:00:00+00:00" }) });
    act(() => {
      app.store.setMe({ ...ME, firm_stopped: true });
      app.store.applyAlgos("acc-a", [algo("A0001")]);
    });
    fireEvent.click(rows()[0]!);
    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    await vi.waitFor(() => expect(posts(calls).map((c) => c.path)).toEqual(["/api/accounts/acc-a/algos/A0001/stop"]));
    expect(app.store.getState().messages.some((m) => m.text.startsWith("Not done"))).toBe(true);
  });

  it("an expanded row shows the algo's figures, settings and child orders", async () => {
    const { app } = setup();
    act(() => {
      app.store.applyOrders("acc-a", [order("MT1", "FILLED", "2026-10-14T04:41:00+00:00", { filled_qty: 75, avg_price_paise: 2501000 }), order("MT2", "OPEN", "2026-10-14T04:41:00+00:00")]);
      app.store.applyAlgos("acc-a", [algo("A0001", { code: "SPREAD", children: ["MT1", "MT2"], params: { leg2: NOV, level_paise: -5000 }, extra: { leg2: NOV, unhedged: 75, traded_spread_paise: null } })]);
    });
    fireEvent.click(screen.getByRole("button", { name: "Show A0001 details" }));
    const detail = screen.getByRole("region", { name: "A0001 details" });
    expect(detail.textContent).toContain("NIFTY26NOVFUT");
    expect(detail.textContent).toContain("Unhedged 75");
    await vi.waitFor(() => expect(within(detail).getByLabelText("Settings").textContent).toBe("Spread (A − B) −50.00")); // by its spec; leg B shown once
    expect(detail.textContent).not.toContain("Traded spread");
    expect(detail.textContent).toContain("MT1");
    expect(detail.textContent).toContain("MT2");
  });

  it("the New form is drawn from the specs and starts an algo", async () => {
    const { calls, app } = setup({}, { instrumentId: FUT });
    fireEvent.click(screen.getByRole("button", { name: "New algo ▾" }));
    const form = await screen.findByRole("form", { name: "Start an algo" });
    expect(within(form).getByText("Works a big order as equal slices on a clock")).toBeTruthy();
    expect((within(form).getByRole("textbox", { name: "Slices" }) as HTMLInputElement).value).toBe("10");
    fireEvent.change(within(form).getByRole("textbox", { name: "Lots" }), { target: { value: "4" } });
    fireEvent.change(within(form).getByRole("combobox", { name: "Price" }), { target: { value: "cross" } });
    fireEvent.change(within(form).getByRole("textbox", { name: "Slices" }), { target: { value: "4" } });
    await within(form).findByRole("combobox", { name: "Product" });
    fireEvent.click(within(form).getByRole("button", { name: /^Start Slicer BUY 300 NIFTY26OCTFUT/ }));
    await vi.waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]!.body).toEqual({ code: "SLICER", instrument_id: FUT, side: "BUY", qty: 300, product: "NRML", params: { slices: 4, mode: "cross" } });
    await vi.waitFor(() => expect(screen.queryByRole("form")).toBeNull());
    expect(app.store.getState().books["acc-a"]!.algos.A0009).toBeTruthy();
    expect(rows()[0]!.getAttribute("aria-selected")).toBe("true");
  });

  it("switching algo redraws the form; problems are listed and nothing is sent", async () => {
    const { calls } = setup({}, { instrumentId: FUT });
    fireEvent.click(screen.getByRole("button", { name: "New algo ▾" }));
    const form = await screen.findByRole("form", { name: "Start an algo" });
    fireEvent.change(within(form).getByRole("combobox", { name: "Algo" }), { target: { value: "SPREAD" } });
    expect(within(form).queryByRole("textbox", { name: "Slices" })).toBeNull();
    expect(within(form).getByRole("combobox", { name: "Leg B" })).toBeTruthy(); // a contract search
    fireEvent.change(within(form).getByRole("combobox", { name: "Algo" }), { target: { value: "SLICER" } });
    expect((within(form).getByRole("textbox", { name: "Slices" }) as HTMLInputElement).value).toBe("10");
    fireEvent.change(within(form).getByRole("textbox", { name: "Slices" }), { target: { value: "3" } });
    fireEvent.change(within(form).getByRole("combobox", { name: "Algo" }), { target: { value: "SPREAD" } });
    fireEvent.change(within(form).getByRole("combobox", { name: "Algo" }), { target: { value: "SLICER" } });
    expect((within(form).getByRole("textbox", { name: "Slices" }) as HTMLInputElement).value).toBe("10"); // each algo starts from its defaults
    fireEvent.change(within(form).getByRole("combobox", { name: "Algo" }), { target: { value: "SPREAD" } });
    fireEvent.click(within(form).getByRole("button", { name: /^Start Spreader/ }));
    expect(within(form).getByRole("alert").textContent).toContain("Leg B is required");
    expect(within(form).getByRole("alert").textContent).toContain("Spread (A − B) is required");
    expect(posts(calls)).toHaveLength(0);
  });

  it("a refusal from the server is shown in the form", async () => {
    setup({ "POST /api/accounts/acc-a/algos": () => ({ status: 422, body: { detail: "Slices must be at most 100" } }) }, { instrumentId: FUT });
    fireEvent.click(screen.getByRole("button", { name: "New algo ▾" }));
    const form = await screen.findByRole("form", { name: "Start an algo" });
    fireEvent.click(within(form).getByRole("button", { name: /^Start Slicer/ }));
    await vi.waitFor(() => expect(within(form).getByRole("alert").textContent).toContain("Slices must be at most 100"));
  });

  it("a protection algo is marked as held by Laddrix", async () => {
    const trail = { code: "TRAILING_STOP", name: "Trailing stop", help: "", family: "protection", params: [] };
    const { app } = setup({ "GET /api/accounts/acc-a/algo-specs": [...SPECS, trail] });
    act(() => app.store.applyAlgos("acc-a", [algo("A0001", { code: "TRAILING_STOP" }), algo("A0002")]));
    await vi.waitFor(() => expect(rows().find((r) => r.textContent!.includes("A0001"))!.textContent).toContain("TRAILING_STOPheld"));
    expect(rows().find((r) => r.textContent!.includes("A0002"))!.textContent).not.toContain("held");
  });

  describe("an algo's own actions (#425)", () => {
    const ACTIONS = [
      { name: "hedge_now", label: "Hedge now", help: "cross the market" },
      { name: "flatten", label: "Flatten", help: "undo" },
    ];
    const url = "/api/accounts/acc-a/algos/A0001/actions";

    it("shows what it would do in the server's words, and Confirm sends exactly that", async () => {
      const lines = ["move SELL 75 NIFTY26NOVFUT to cross the market"];
      const { app, calls } = setup({
        [`GET ${url}/hedge_now`]: { name: "hedge_now", label: "Hedge now", lines, ok: true, why: "" },
        [`POST ${url}/hedge_now`]: () => ({ status: 200, body: algo("A0001", { actions: ACTIONS, status: "hedging now" }) }),
      });
      act(() => app.store.applyAlgos("acc-a", [algo("A0001", { actions: ACTIONS })]));
      fireEvent.click(rows()[0]!);
      fireEvent.click(screen.getByRole("button", { name: "Hedge now…" }));
      const dialog = await screen.findByRole("dialog", { name: "Hedge now A0001" });
      expect(dialog.textContent).toContain("move SELL 75 NIFTY26NOVFUT to cross the market");
      fireEvent.click(within(dialog).getByRole("button", { name: "Confirm" }));
      await screen.findByText("hedging now");
      expect(posts(calls).at(-1)).toMatchObject({ path: `${url}/hedge_now`, body: { expect: lines } });
      expect(screen.queryByRole("dialog")).toBeNull();
    });

    it("says why it can't be done, with nothing to confirm", async () => {
      const { app, calls } = setup({ [`GET ${url}/flatten`]: { name: "flatten", label: "Flatten", lines: [], ok: false, why: "nothing is unhedged: use Stop" } });
      act(() => app.store.applyAlgos("acc-a", [algo("A0001", { actions: ACTIONS })]));
      fireEvent.click(rows()[0]!);
      fireEvent.click(screen.getByRole("button", { name: "Flatten…" }));
      const dialog = await screen.findByRole("dialog", { name: "Flatten A0001" });
      expect(dialog.textContent).toContain("nothing is unhedged: use Stop");
      expect(within(dialog).queryByRole("button", { name: "Confirm" })).toBeNull();
      fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
      expect(posts(calls)).toEqual([]);
    });

    it("offers no actions on an algo that has none or has ended", () => {
      const { app } = setup();
      act(() => app.store.applyAlgos("acc-a", [algo("A0001"), algo("A0002", { actions: ACTIONS, state: "DONE" })]));
      for (const r of rows()) {
        fireEvent.click(r);
        expect(screen.queryByRole("button", { name: "Hedge now…" })).toBeNull();
      }
    });
  });
});
