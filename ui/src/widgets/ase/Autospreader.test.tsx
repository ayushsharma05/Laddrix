import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Spread } from "../../api/types";
import { AppProvider, createApp } from "../../app/context";
import { fakeRest, fakeStream, tick } from "../../test/fakes";
import { Autospreader, type AutospreaderParams } from "./Autospreader";

const NOV = { id: "NFO:NIFTY26NOVFUT", exchange: "NFO", symbol: "NIFTY26NOVFUT", kind: "FUT", tick_paise: 10, lot_size: 75, expiry: "2026-11-24", strike_paise: null, underlying: "NIFTY", tradable: true };
const DEC = { ...NOV, id: "NFO:NIFTY26DECFUT", symbol: "NIFTY26DECFUT", expiry: "2026-12-29" };
const CAL: Spread = {
  spread_id: "s1",
  instrument_id: "SPREAD:s1",
  name: "NIFTY Nov/Dec",
  tick_paise: 10,
  created_at: "2026-10-24T09:00:00+00:00",
  updated_at: "2026-10-24T09:00:00+00:00",
  legs: [
    { instrument_id: NOV.id, ratio: 1, multiplier: 1, quote: true, payup_ticks: 2 },
    { instrument_id: DEC.id, ratio: -1, multiplier: -1, quote: false, payup_ticks: 2 },
  ],
};

function setup(spreads: Spread[], params: Partial<AutospreaderParams> = {}) {
  const posted: unknown[] = [];
  const server = fakeRest({
    "GET /api/spreads": spreads,
    "GET /api/instruments": (_b: unknown) => ({ status: 200, body: [NOV, DEC] }),
    "POST /api/spreads": (body: unknown) => {
      posted.push(body);
      const b = body as { name: string };
      if (!b.name) return { status: 422, body: { detail: "the name must be 1 to 40 characters; choose exactly one leg to quote" } };
      return { status: 201, body: { ...CAL, spread_id: "s2", instrument_id: "SPREAD:s2", name: b.name } };
    },
    "PUT /api/spreads/s1": (body: unknown) => {
      posted.push(body);
      return { status: 200, body: { ...CAL, name: (body as { name: string }).name } };
    },
    "DELETE /api/spreads/s1": () => ({ status: 204 }),
  });
  const { stream, socket } = fakeStream();
  const app = createApp({ rest: server.rest, stream });
  stream.start();
  socket().open();
  const setParams = vi.fn();
  const open = vi.fn();
  const props = { params: { link: "none" as const, ...params }, setParams, setInstrument: vi.fn(), publish: vi.fn(), open };
  const view = render(
    <AppProvider app={app}>
      <Autospreader {...props} />
    </AppProvider>,
  );
  const rerender = (next: Partial<AutospreaderParams>) =>
    view.rerender(
      <AppProvider app={app}>
        <Autospreader {...props} params={{ ...props.params, ...next }} />
      </AppProvider>,
    );
  return { setParams, open, socket, posted, rerender, calls: server.calls };
}

describe("the Autospreader", () => {
  it("lists my spreads with what they buy and sell and their live implied price", async () => {
    const { socket } = setup([CAL]);
    expect(await screen.findByText("NIFTY Nov/Dec")).toBeTruthy();
    expect(screen.getByText("+1 NIFTY26NOVFUT −1 NIFTY26DECFUT")).toBeTruthy();
    expect(socket().sent).toContainEqual({ op: "subscribe", instruments: ["SPREAD:s1"] });
    expect(screen.getByLabelText("NIFTY Nov/Dec bid").textContent).toBe("–"); // no price yet
    act(() => socket().receive({ type: "ticks", ticks: [tick("SPREAD:s1", -15000, { bids: [[-15200, 10, 0]], asks: [[-14900, 2, 0]] })] }));
    expect(screen.getByLabelText("NIFTY Nov/Dec bid").textContent).toBe("−152.00 (10)");
    expect(screen.getByLabelText("NIFTY Nov/Dec ask").textContent).toBe("−149.00 (2)");
  });

  it("creates a spread from the form: legs picked by search, the server's rules said in words", async () => {
    const { posted, rerender, setParams } = setup([], { mode: "edit" });
    await screen.findByLabelText("Spread name");
    fireEvent.click(screen.getByRole("button", { name: "Create spread" }));
    expect(screen.getByText("leg 1: pick its contract")).toBeTruthy(); // said here: nothing sent
    expect(posted).toEqual([]);
    fireEvent.change(screen.getByLabelText("Leg 1 contract"), { target: { value: "nov" } });
    fireEvent.mouseDown(await screen.findByRole("option", { name: /NIFTY26NOVFUT/ }));
    fireEvent.change(screen.getByLabelText("Leg 2 contract"), { target: { value: "dec" } });
    fireEvent.mouseDown(await screen.findByRole("option", { name: /NIFTY26DECFUT/ }));
    fireEvent.click(screen.getByRole("button", { name: "Create spread" }));
    expect(await screen.findByText("the name must be 1 to 40 characters")).toBeTruthy(); // the server's words
    expect(screen.getByText("choose exactly one leg to quote")).toBeTruthy(); // each problem on its own line
    fireEvent.change(screen.getByLabelText("Spread name"), { target: { value: "NIFTY Nov/Dec" } });
    expect(screen.getByText("price = NIFTY26NOVFUT − NIFTY26DECFUT")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Leg 2 pay-up ticks"), { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: "Create spread" }));
    await waitFor(() => expect(setParams).toHaveBeenCalledWith({ mode: "list", spreadId: undefined }));
    expect(posted.at(-1)).toEqual({ name: "NIFTY Nov/Dec", legs: [CAL.legs[0], { ...CAL.legs[1], payup_ticks: 4 }] });
    rerender({ mode: "list" });
    expect(await screen.findByText("NIFTY Nov/Dec")).toBeTruthy(); // in the list now
  });

  it("adds a third leg for a butterfly and moves the quote", async () => {
    const { posted } = setup([CAL], { mode: "edit", spreadId: "s1" });
    await screen.findByLabelText("Edit NIFTY Nov/Dec");
    fireEvent.click(screen.getByRole("button", { name: "+ Add leg" }));
    fireEvent.change(screen.getByLabelText("Leg 2 ratio"), { target: { value: "-2" } });
    fireEvent.click(screen.getByLabelText("Quote leg 2"));
    fireEvent.change(screen.getByLabelText("Leg 3 contract"), { target: { value: "nov" } });
    fireEvent.mouseDown(await screen.findByRole("option", { name: /NIFTY26NOVFUT/ }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect((posted[0] as { legs: unknown[] }).legs).toEqual([
      { ...CAL.legs[0], quote: false },
      { ...CAL.legs[1], ratio: -2, multiplier: -2, quote: true },
      { instrument_id: NOV.id, ratio: 1, multiplier: 1, quote: false, payup_ticks: 2 },
    ]);
  });

  it("deletes only after a second click", async () => {
    const { calls } = setup([CAL]);
    fireEvent.click(await screen.findByRole("button", { name: "Delete…" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    await waitFor(() => expect(screen.queryByText("NIFTY Nov/Dec")).toBeNull());
    expect(calls.filter((c) => c.method === "DELETE").map((c) => c.path)).toEqual(["/api/spreads/s1"]);
  });

  it("opens a spread's ladder from its row, to trade it", async () => {
    const { open } = setup([CAL]);
    fireEvent.click(await screen.findByRole("button", { name: "Ladder" }));
    expect(open).toHaveBeenCalledWith("ladder", { instrumentId: "SPREAD:s1", link: "none" });
  });

  it("opens a spread to edit from its row", async () => {
    const { setParams } = setup([CAL]);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    expect(setParams).toHaveBeenCalledWith({ mode: "edit", spreadId: "s1" });
  });
});
