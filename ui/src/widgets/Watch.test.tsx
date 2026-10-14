import { act, fireEvent, render, screen } from "@testing-library/react";
import { AppProvider, createApp } from "../app/context";
import { fakeRest, fakeStream, tick } from "../test/fakes";
import { Watch, type WatchParams } from "./Watch";

const NIFTY = { id: "NFO:NIFTY26OCTFUT", exchange: "NFO", symbol: "NIFTY26OCTFUT", kind: "FUT", tick_paise: 10, lot_size: 75, expiry: "2026-10-27", strike_paise: null, underlying: "NIFTY", tradable: true };
const SBIN = { id: "NSE:SBIN", exchange: "NSE", symbol: "SBIN", kind: "EQ", tick_paise: 5, lot_size: 1, expiry: null, strike_paise: null, underlying: null, tradable: true };

function setup(items: string[]) {
  const server = fakeRest({
    [`GET /api/instruments/${encodeURIComponent(NIFTY.id)}`]: NIFTY,
    [`GET /api/instruments/${encodeURIComponent(SBIN.id)}`]: SBIN,
    "GET /api/instruments": [SBIN],
  });
  const { stream, socket } = fakeStream();
  const app = createApp({ rest: server.rest, stream });
  stream.start();
  socket().open();
  const props = {
    params: { link: "pink", items } as WatchParams,
    setParams: vi.fn(),
    setInstrument: vi.fn(),
    publish: vi.fn(),
    open: vi.fn(),
  };
  render(
    <AppProvider app={app}>
      <Watch {...props} />
    </AppProvider>,
  );
  return { ...props, socket, stream, app };
}

describe("Watch", () => {
  it("shows each row's last price and change from the previous close", async () => {
    const { socket } = setup([NIFTY.id]);
    expect(await screen.findByText("NIFTY26OCTFUT")).toBeTruthy();
    act(() => {
      socket().receive({
        type: "ticks",
        ticks: [tick(NIFTY.id, 2501010, { close_paise: 2491480, bids: [[2501000, 150, 2]], asks: [[2501010, 75, 1]] })],
      });
    });
    const row = screen.getByRole("row", { name: /NIFTY26OCTFUT/ });
    expect(row.textContent).toContain("25,010.10");
    expect(row.textContent).toContain("+95.30");
    expect(row.textContent).toContain("+0.38%");
    expect(row.querySelector(".good")).not.toBeNull();
  });

  it("shows a fall in red with a minus sign", async () => {
    const { socket } = setup([NIFTY.id]);
    await screen.findByText("NIFTY26OCTFUT");
    act(() => socket().receive({ type: "ticks", ticks: [tick(NIFTY.id, 2490000, { close_paise: 2500000 })] }));
    const row = screen.getByRole("row", { name: /NIFTY26OCTFUT/ });
    expect(row.textContent).toContain("−100.00");
    expect(row.querySelector(".bad")).not.toBeNull();
  });

  it("subscribes once per instrument and unsubscribes when a row goes", async () => {
    const { socket, stream } = setup([NIFTY.id, SBIN.id]);
    await screen.findByText("SBIN");
    expect(socket().sent).toEqual([
      { op: "subscribe", instruments: [NIFTY.id] },
      { op: "subscribe", instruments: [SBIN.id] },
    ]);
    expect(stream.watched().sort()).toEqual([NIFTY.id, SBIN.id].sort());
  });

  it("sends a clicked row to the link group and opens a ladder on double-click", async () => {
    const { publish, open } = setup([NIFTY.id, SBIN.id]);
    fireEvent.click(await screen.findByText("SBIN"));
    expect(publish).toHaveBeenCalledWith(SBIN.id);
    fireEvent.doubleClick(screen.getByText("SBIN"));
    expect(open).toHaveBeenCalledWith("ladder", { instrumentId: SBIN.id, link: "none" });
  });

  it("moves through rows with the arrow keys", async () => {
    const { publish } = setup([NIFTY.id, SBIN.id]);
    await screen.findByText("SBIN");
    const list = document.querySelector(".watch") as HTMLElement;
    fireEvent.keyDown(list, { key: "ArrowDown" });
    fireEvent.keyDown(list, { key: "ArrowDown" });
    fireEvent.keyDown(list, { key: "ArrowDown" }); // stays on the last
    expect(publish.mock.calls.map((c) => c[0])).toEqual([NIFTY.id, SBIN.id, SBIN.id]);
  });

  it("removes a row", async () => {
    const { setParams } = setup([NIFTY.id, SBIN.id]);
    fireEvent.click(await screen.findByLabelText("Remove SBIN"));
    expect(setParams).toHaveBeenCalledWith({ items: [NIFTY.id] });
  });

  it("adds a symbol from the search box, once", async () => {
    const { setParams } = setup([NIFTY.id]);
    const input = screen.getByLabelText("Add to watch list");
    fireEvent.change(input, { target: { value: "sb" } });
    const option = await screen.findByRole("option", { name: /SBIN/ });
    fireEvent.mouseDown(option);
    expect(setParams).toHaveBeenCalledWith({ items: [NIFTY.id, SBIN.id] });
  });
});
