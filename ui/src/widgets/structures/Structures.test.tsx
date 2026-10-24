import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Structures as Body, StructureView } from "../../api/types";
import { AppProvider, createApp } from "../../app/context";
import { fakeRest, fakeStream } from "../../test/fakes";
import { ME, order } from "../../test/fixtures";
import { Structures } from "./Structures";

const OCT = "NFO:NIFTY26OCTFUT";
const NOV = "NFO:NIFTY26NOVFUT";
const CAL: StructureView = {
  structure_id: "s1",
  name: "NIFTY Oct/Nov",
  spread_id: "sp1",
  created_by: "ayush",
  created_at: "2026-10-24T05:00:00+00:00",
  lots: 1,
  avg_spread_paise: -13000,
  mark_paise: -12860,
  realised_paise: 75000,
  unrealised_paise: 10500,
  pnl_paise: 85500,
  note: "",
  legs: [
    { instrument_id: OCT, ratio: 1, multiplier: 1, qty: 75, avg_price_paise: 2501000, ltp_paise: 2502000, realised_paise: 75000, unrealised_paise: 7500, residual_qty: 0 },
    { instrument_id: NOV, ratio: -1, multiplier: -1, qty: -75, avg_price_paise: 2514000, ltp_paise: 2513600, realised_paise: 0, unrealised_paise: 3000, residual_qty: 0 },
  ],
  allocations: [{ client_order_id: "MT12", qty: 75, instrument_id: OCT, side: "BUY", avg_price_paise: 2501000 }],
};
const OTHER: StructureView = { ...CAL, structure_id: "s2", name: "by hand", lots: 0, pnl_paise: 0, realised_paise: 0, allocations: [], legs: CAL.legs.map((g) => ({ ...g, qty: 0 })) };
const BODY: Body = {
  structures: [CAL, OTHER],
  unallocated: [{ instrument_id: OCT, qty: 75 }],
  free_fills: [{ client_order_id: "MT20", instrument_id: OCT, side: "BUY", filled_qty: 75, unallocated_qty: 75, avg_price_paise: 2501500 }],
};

function setup(routes: Record<string, unknown> = {}) {
  const url = "/api/accounts/acc-a/structures";
  const server = fakeRest({
    [`GET ${url}`]: BODY,
    "GET /api/spreads": [{ spread_id: "sp1", instrument_id: "SPREAD:sp1", name: "NIFTY Oct/Nov", legs: [], tick_paise: 10, created_at: "", updated_at: "" }],
    [`POST ${url}`]: () => ({ status: 201, body: CAL }),
    [`POST ${url}/s1/allocations/remove`]: () => ({ status: 200, body: CAL }),
    [`POST ${url}/s1/move`]: () => ({ status: 200, body: {} }),
    [`POST ${url}/s2/allocations`]: () => ({ status: 422, body: { detail: "MT20 has 75 filled not yet in a structure, not 150" } }),
    [`DELETE ${url}/s2`]: () => ({ status: 204 }),
    ...routes,
  });
  const { stream, socket } = fakeStream();
  const app = createApp({ rest: server.rest, stream });
  app.store.setMe(ME);
  app.store.selectAccount("acc-a");
  stream.start();
  socket().open();
  const props = { params: { link: "none" as const }, setParams: vi.fn(), setInstrument: vi.fn(), publish: vi.fn(), open: vi.fn() };
  render(
    <AppProvider app={app}>
      <Structures {...props} />
    </AppProvider>,
  );
  return { ...server, url, app };
}

const posts = (calls: { method: string; path: string; body: unknown }[]) => calls.filter((c) => c.method !== "GET");

describe("Structures (#428)", () => {
  it("shows each structure's lots and P&L as the server worked them out, and what is unallocated", async () => {
    setup();
    expect((await screen.findByLabelText("NIFTY Oct/Nov lots")).textContent).toBe("+1 lot");
    expect(screen.getByLabelText("NIFTY Oct/Nov P&L").textContent).toBe("P&L +₹855.00");
    expect(screen.getByRole("group", { name: "Unallocated" }).textContent).toContain("NIFTY26OCTFUT +75");
  });

  it("opens a structure on its legs, residuals and allocations; remove and move ask the server", async () => {
    const { calls, url } = setup();
    fireEvent.click(await screen.findByRole("button", { name: "Open NIFTY Oct/Nov" }));
    expect(screen.getByLabelText("NIFTY26OCTFUT residual").textContent).toBe("residual 0");
    expect(screen.getByRole("group", { name: "NIFTY Oct/Nov" }).textContent).toContain("75 NIFTY26OCTFUT"); // MT12's 75
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]).toMatchObject({ path: `${url}/s1/allocations/remove`, body: { client_order_id: "MT12", qty: 75 } });
    fireEvent.change(screen.getByLabelText("Move MT12 to"), { target: { value: "s2" } });
    await waitFor(() => expect(posts(calls)).toHaveLength(2));
    expect(posts(calls)[1]).toMatchObject({ path: `${url}/s1/move`, body: { to: "s2", client_order_id: "MT12", qty: 75 } });
  });

  it("adds a free fill to a structure; the server's refusal is said in its words", async () => {
    const { calls, url } = setup();
    fireEvent.change(await screen.findByLabelText("Quantity of MT20"), { target: { value: "150" } });
    fireEvent.change(screen.getByLabelText("Add MT20 to"), { target: { value: "s2" } });
    expect((await screen.findByRole("alert")).textContent).toBe("MT20 has 75 filled not yet in a structure, not 150");
    expect(posts(calls)[0]).toMatchObject({ path: `${url}/s2/allocations`, body: { client_order_id: "MT20", qty: 150 } });
  });

  it("asks again when the account's orders change (a fill, an exit)", async () => {
    const { calls, url, app } = setup();
    await screen.findByLabelText("NIFTY Oct/Nov lots");
    const before = calls.filter((c) => c.path === url).length;
    act(() => app.store.applyOrders("acc-a", [order("MT30", "FILLED", "2026-10-24T05:00:01+00:00")]));
    await waitFor(() => expect(calls.filter((c) => c.path === url).length).toBe(before + 1));
  });

  it("makes a structure from one of my spreads, and deletes an empty one", async () => {
    const { calls, url } = setup();
    await screen.findByLabelText("NIFTY Oct/Nov lots");
    await act(async () => {
      fireEvent.change(screen.getByLabelText("New structure from a spread"), { target: { value: "sp1" } });
    });
    expect(posts(calls)[0]).toMatchObject({ path: url, body: { spread_id: "sp1" } });
    fireEvent.click(screen.getByRole("button", { name: "Open by hand" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete (empty)" }));
    await waitFor(() => expect(posts(calls).at(-1)).toMatchObject({ method: "DELETE", path: `${url}/s2` }));
  });
});
