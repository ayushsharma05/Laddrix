import { act, fireEvent, render, screen, within } from "@testing-library/react";
import type { GttSpec, GttView } from "../../api/types";
import { AppProvider, createApp } from "../../app/context";
import { fakeRest, fakeStream } from "../../test/fakes";
import { ME } from "../../test/fixtures";
import { GttBook } from "./GttBook";

const FUT = "NFO:NIFTY26OCTFUT";
const NIFTY = { id: FUT, exchange: "NFO", symbol: "NIFTY26OCTFUT", kind: "FUT", tick_paise: 10, lot_size: 75, expiry: "2026-10-27", strike_paise: null, underlying: "NIFTY", tradable: true, ltp_paise: 2501000 };
const SPEC: GttSpec = {
  instrument_id: FUT,
  kinds: ["single", "two-leg"],
  legs: { single: 1, "two-leg": 2 },
  labels: { single: "Single", "two-leg": "OCO (stop + target)" },
  tick_paise: 10,
  lot_size: 75,
  products: ["NRML", "MIS"],
  min_distance_pct: 0.25,
  expiry_days: 365,
  max_active: 250,
  help: { single: "One trigger, held by the broker", "two-leg": "A stop and a target" },
};

function gtt(id: string, extra: Partial<GttView> = {}): GttView {
  return {
    gtt_id: id,
    account_id: "acc-a",
    instrument_id: FUT,
    product: "NRML",
    kind: "single",
    legs: [{ trigger_paise: 2480000, side: "SELL", qty: 75, price_paise: 2479500 }],
    status: "active",
    order_id: null,
    fired_leg: null,
    reason: "",
    created_at: "2026-10-15T04:00:00+00:00",
    updated_at: "2026-10-15T04:00:00+00:00",
    ...extra,
  };
}

function setup(routes: Record<string, unknown> = {}) {
  const server = fakeRest({
    [`GET /api/accounts/acc-a/gtt-spec`]: SPEC,
    [`GET /api/instruments/${encodeURIComponent(FUT)}`]: NIFTY,
    "POST /api/accounts/acc-a/gtt": (body: unknown) => {
      const b = body as Pick<GttView, "kind" | "legs" | "product">;
      return { status: 201, body: gtt("G7", { kind: b.kind, legs: b.legs, product: b.product }) };
    },
    "DELETE /api/accounts/acc-a/gtt/G1": gtt("G1", { status: "deleted", updated_at: "2026-10-15T05:00:00+00:00" }),
    ...routes,
  });
  const { stream, socket } = fakeStream();
  const app = createApp({ rest: server.rest, stream });
  app.store.setMe(ME);
  app.store.selectAccount("acc-a");
  stream.start();
  socket().open();
  const props = { params: { link: "pink" as const, instrumentId: FUT }, setParams: vi.fn(), setInstrument: vi.fn(), publish: vi.fn(), open: vi.fn() };
  render(
    <AppProvider app={app}>
      <GttBook {...props} />
    </AppProvider>,
  );
  return { ...server, ...props, app, socket };
}

const rows = () => within(screen.getByRole("grid", { name: "GTTs" })).queryAllByRole("row");

describe("GTT book", () => {
  it("lists GTTs live, and a trigger reaches Messages with the broker's order", () => {
    const { app, socket, publish } = setup();
    act(() => app.store.applyGtts("acc-a", [gtt("G1")]));
    expect(rows()[0]!.textContent).toContain("SELL 75 @ 24,795.00 when 24,800.00");
    expect(rows()[0]!.textContent).toContain("Active");
    act(() => socket().receive({ type: "gtt", account_id: "acc-a", gtt: gtt("G1", { status: "triggered", order_id: "B42", fired_leg: 0, updated_at: "2026-10-15T05:00:00+00:00" }) }));
    expect(rows()[0]!.textContent).toContain("Triggered · leg 1");
    expect(app.store.getState().messages[0]!.text).toBe("GTT G1 NIFTY26OCTFUT triggered: the broker placed order B42 (leg 1)");
    fireEvent.click(rows()[0]!);
    expect(publish).toHaveBeenCalledWith(FUT);
  });

  it("places a GTT from a form drawn from the broker's spec", async () => {
    const { calls, app } = setup();
    fireEvent.click(screen.getByRole("button", { name: /New GTT/ }));
    await screen.findByRole("combobox", { name: "GTT kind" });
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(["Single", "OCO (stop + target)", "NRML", "MIS"]);
    expect(screen.getByText("One trigger, held by the broker")).toBeTruthy();
    fireEvent.change(screen.getByRole("combobox", { name: "GTT kind" }), { target: { value: "two-leg" } });
    // filled from the market once its price is known (it may arrive after the form opens)
    await vi.waitFor(() => expect((screen.getByRole("textbox", { name: "Stop (lower) trigger" }) as HTMLInputElement).value).toBe("24,759.90"));
    expect((screen.getByRole("textbox", { name: "Target (upper) trigger" }) as HTMLInputElement).value).toBe("25,260.10");
    fireEvent.change(screen.getByRole("textbox", { name: "GTT lots" }), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: /Place GTT SELL 150/ }));
    await vi.waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(1));
    const body = calls.find((c) => c.method === "POST")!.body as { kind: string; legs: { qty: number; trigger_paise: number }[] };
    expect(body.kind).toBe("two-leg");
    expect(body.legs.map((l) => [l.qty, l.trigger_paise])).toEqual([
      [150, 2475990],
      [150, 2526010],
    ]);
    await vi.waitFor(() => expect(app.store.getState().books["acc-a"]!.gtts.G7).toBeTruthy());
  });

  it("shows problems in words before anything is sent", async () => {
    const { calls } = setup();
    fireEvent.click(screen.getByRole("button", { name: /New GTT/ }));
    const trigger = await screen.findByRole("textbox", { name: "GTT trigger" });
    fireEvent.change(trigger, { target: { value: "25,000" } });
    fireEvent.click(screen.getByRole("button", { name: /Place GTT/ }));
    expect((await screen.findByRole("alert")).textContent).toContain("only 0.04% from the last price");
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
  });

  it("deletes the selected active GTT", async () => {
    const { app, calls } = setup();
    act(() => app.store.applyGtts("acc-a", [gtt("G1")]));
    const del = screen.getByRole("button", { name: "Delete" }) as HTMLButtonElement;
    expect(del.disabled).toBe(true);
    fireEvent.click(rows()[0]!);
    fireEvent.click(del);
    await vi.waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path === "/api/accounts/acc-a/gtt/G1")).toBe(true));
    await vi.waitFor(() => expect(rows()[0]!.textContent).toContain("Deleted"));
    expect(del.disabled).toBe(true); // a finished GTT can't be deleted again
  });

  it("says so when the broker holds no GTTs", async () => {
    setup({ "GET /api/accounts/acc-a/gtt-spec": (() => ({ status: 404, body: { detail: "fake doesn't hold GTT orders" } })) as unknown });
    fireEvent.click(screen.getByRole("button", { name: /New GTT/ }));
    expect(await screen.findByText(/GTTs unavailable: fake doesn't hold GTT orders/)).toBeTruthy();
  });
});
