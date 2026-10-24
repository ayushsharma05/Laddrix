// A spread ladder (#416): the normal ladder on a spread "SPREAD:<id>". Its clicks start the
// Autospreader algo; its Work column shows those algos; the right-click menu opens the
// spread's definition.
import { act, fireEvent, render, screen } from "@testing-library/react";
import { AppProvider, createApp } from "../../app/context";
import { fakeRest, fakeStream, tick } from "../../test/fakes";
import { ME, algo } from "../../test/fixtures";
import { widget } from "../../workspace/registry";
import { Ladder, type LadderParams } from "./Ladder";
import { spreadWorking } from "./model";

const ID = "SPREAD:s1";
const SPREAD = {
  id: ID,
  exchange: "SPREAD",
  symbol: "NIFTY Oct/Nov",
  kind: "SPREAD",
  tick_paise: 10,
  lot_size: 1,
  expiry: null,
  strike_paise: null,
  underlying: null,
  tradable: true,
  ltp_paise: -13000,
  spread: { spread_id: "s1", legs: [] },
};
const BOOK = tick(ID, -13000, { bids: [[-13020, 4, 0]], asks: [[-12990, 2, 0]] }); // spread prices: negative
const working = (id: string, extra: Record<string, unknown> = {}) =>
  algo(id, { code: "ASE", side: "SELL", qty: 300, params: { spread: "s1", level_paise: -13050 }, extra: { lots: 2, lots_done: 0, spread_side: "BUY", ...extra } });

function setup() {
  const server = fakeRest({
    [`GET /api/instruments/${encodeURIComponent(ID)}`]: SPREAD,
    "POST /api/accounts/acc-a/spreads/s1/orders": (body: unknown) => ({ status: 201, body: working("A9", { lots: (body as { lots: number }).lots }) }),
    "POST /api/accounts/acc-a/algos/A1/stop": () => ({ status: 200, body: { ...working("A1"), state: "STOPPING" } }),
  });
  const { stream, socket } = fakeStream();
  const app = createApp({ rest: server.rest, stream });
  app.store.setMe(ME);
  stream.start();
  socket().open();
  const params = { link: "none", instrumentId: ID, lots: 2, type: "LMT", tif: "DAY", confirmOverPaise: 0 } as LadderParams;
  render(
    <AppProvider app={app}>
      <Ladder params={params} setParams={vi.fn()} setInstrument={vi.fn()} publish={vi.fn()} open={vi.fn()} />
    </AppProvider>,
  );
  const push = () => act(() => socket().receive({ type: "ticks", ticks: [BOOK] }));
  return { ...server, app, push };
}

const posts = (calls: { method: string; path: string; body: unknown }[]) => calls.filter((c) => c.method !== "GET");

describe("a spread ladder", () => {
  it("shows the spread's implied prices, negative ones too, and asks for no order spec or VAP", async () => {
    const { push, calls } = setup();
    await screen.findByText("−130.00", { selector: ".price" });
    push();
    expect(screen.getByLabelText("Buy at −130.20")).toBeTruthy();
    expect(calls.some((c) => c.path.includes("order-spec") || c.path.endsWith("/vap"))).toBe(false); // the server's legs, not ours
    expect(screen.queryByLabelText("Type")).toBeNull(); // no order type, TIF or product to pick
    expect(screen.getByText("spread lots")).toBeTruthy();
  });

  it("a click buys or sells spread lots at that spread price: the Autospreader works the legs", async () => {
    const { push, calls, app } = setup();
    await screen.findByText("−130.00", { selector: ".price" });
    push();
    fireEvent.click(screen.getByLabelText("Buy at −130.50"));
    fireEvent.click(screen.getByLabelText("Sell at −129.80"));
    await vi.waitFor(() => expect(posts(calls)).toHaveLength(2));
    expect(posts(calls).map((c) => [c.path, c.body])).toEqual([
      ["/api/accounts/acc-a/spreads/s1/orders", { side: "BUY", lots: 2, price_paise: -13050 }],
      ["/api/accounts/acc-a/spreads/s1/orders", { side: "SELL", lots: 2, price_paise: -12980 }],
    ]);
    await vi.waitFor(() => expect(app.store.getState().books["acc-a"]?.algos.A9).toBeTruthy());
  });

  it("shows a running spread order in Work, and cancels it by stopping its algo", async () => {
    const { push, calls, app } = setup();
    await screen.findByText("−130.00", { selector: ".price" });
    push();
    act(() => app.store.applyAlgos("acc-a", [working("A1")]));
    fireEvent.click(screen.getByLabelText("Select my orders at −130.50"));
    fireEvent.click(screen.getByLabelText("Cancel my orders at −130.50"));
    await vi.waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]).toMatchObject({ method: "POST", path: "/api/accounts/acc-a/algos/A1/stop" });
  });

  it("moves a running spread order: stops it and starts the lots left at the new price", async () => {
    const { push, calls, app } = setup();
    await screen.findByText("−130.00", { selector: ".price" });
    push();
    act(() => app.store.applyAlgos("acc-a", [working("A1", { lots: 3, lots_done: 1 })]));
    fireEvent.click(screen.getByLabelText("Select my orders at −130.50"));
    fireEvent.click(screen.getByLabelText("Move selected orders to −130.30"));
    await vi.waitFor(() => expect(posts(calls)).toHaveLength(2));
    expect(posts(calls).map((c) => [c.path, c.body])).toEqual([
      ["/api/accounts/acc-a/algos/A1/stop", {}],
      ["/api/accounts/acc-a/spreads/s1/orders", { side: "BUY", lots: 2, price_paise: -13030 }],
    ]);
  });

  it("offers Autospreader settings in its right-click menu", () => {
    const open = vi.fn();
    const items = widget("ladder")!.menu!({ link: "none", instrumentId: ID, lots: 1 } as LadderParams, { setParams: vi.fn(), open });
    const first = items[0] as { label: string; onSelect: () => void };
    expect(first.label).toBe("Autospreader settings…");
    first.onSelect();
    expect(open).toHaveBeenCalledWith("ase", { mode: "edit", spreadId: "s1" });
    const plain = widget("ladder")!.menu!({ link: "none", instrumentId: "NFO:X", lots: 1 } as LadderParams, { setParams: vi.fn(), open });
    expect(plain.some((i) => typeof i === "object" && "label" in i && i.label.startsWith("Autospreader"))).toBe(false);
  });
});

describe("a spread's working orders", () => {
  it("are its running ASE algos, at their spread price, in spread lots", () => {
    const algos = {
      A1: working("A1", { lots: 3, lots_done: 1, spread_side: "SELL" }),
      A2: { ...working("A2"), state: "STOPPING" as const },
      A3: { ...working("A3"), state: "DONE" as const },
      A4: { ...working("A4"), params: { spread: "other", level_paise: 0 } },
      A5: algo("A5"), // a slicer
      A6: working("A6", { lots: 2, lots_done: 2 }), // every lot quoted and filled: nothing left
    };
    const shown = spreadWorking(algos, ID);
    expect(shown.map((o) => [o.request.client_order_id, o.request.side, o.request.qty, o.filled_qty, o.request.price_paise, o.status, o.pending])).toEqual([
      ["A1", "SELL", 3, 1, -13050, "PARTIAL", "NONE"],
      ["A2", "BUY", 2, 0, -13050, "OPEN", "CANCEL"],
    ]);
  });
});
