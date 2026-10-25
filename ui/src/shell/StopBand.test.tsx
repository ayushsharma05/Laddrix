import { act, fireEvent, render, screen } from "@testing-library/react";
import { AppProvider, createApp } from "../app/context";
import { fakeRest, fakeStream } from "../test/fakes";
import { ME } from "../test/fixtures";
import { Bands } from "./Bands";
import type { FlattenStatus } from "./StopBand";

const FUT = "NFO:NIFTY26OCTFUT";
const BASE = "/api/accounts/acc-a/flatten";
const STATUS: FlattenStatus = {
  stopped: true,
  stop: { reason: "feeling unsure", by: "ayush", at: null },
  stop_report: {
    lines: [
      "stopped by ayush: feeling unsure",
      "1 working order asked to cancel (MT4)",
      "1 algo stopped",
      "positions kept: NIFTY26OCTFUT MIS +150; use Flatten to close them",
    ],
  },
  positions: [{ instrument_id: FUT, product: "MIS", qty: 150, closing_qty: 0, unknown: null }],
  can_flatten: true,
  running: false,
  explain: ["STOP … It does not close positions.", "Flatten closes open positions …"],
};

function setup(routes: Record<string, unknown>) {
  const server = fakeRest(routes);
  const { stream, socket } = fakeStream();
  const app = createApp({ rest: server.rest, stream });
  app.store.setMe(ME);
  app.store.selectAccount("acc-a");
  stream.start();
  socket().open();
  render(
    <AppProvider app={app}>
      <Bands />
    </AppProvider>,
  );
  act(() => socket().receive({ type: "kill", scope: "account acc-a", engaged: true, reason: "feeling unsure", by: "ayush", at: null }));
  return { ...server, app, socket };
}

const posts = (calls: { method: string; path: string; body: unknown }[]) => calls.filter((c) => c.method === "POST");

describe("the STOP band", () => {
  it("says what the STOP did in the server's words and flattens after one confirmation", async () => {
    const done = { positions: [{ instrument_id: FUT, orders: ["MT9"], refused: [], left_open: 0, closed: true, summary: `closing +150 ${FUT} MIS: sent MT9` }], summary: "" };
    const { calls, app } = setup({ [`GET ${BASE}`]: STATUS, [`POST ${BASE}`]: done });
    await screen.findByText("1 working order asked to cancel (MT4) · 1 algo stopped · positions kept: NIFTY26OCTFUT MIS +150; use Flatten to close them");
    fireEvent.click(screen.getByRole("button", { name: "Flatten (1)" }));
    expect(posts(calls)).toHaveLength(0); // the first click only asks
    fireEvent.click(screen.getByRole("button", { name: "Confirm: close 1 position" }));
    await vi.waitFor(() => expect(posts(calls)).toEqual([{ method: "POST", path: BASE, body: { confirm: true } }]));
    await vi.waitFor(() => expect(app.store.getState().messages[0]).toMatchObject({ level: "INFO", text: `Flatten: closing +150 ${FUT} MIS: sent MT9` }));
  });

  it("a confirmation is only for the positions it was shown with: a change asks again", async () => {
    const { calls, app } = setup({ [`GET ${BASE}`]: STATUS });
    fireEvent.click(await screen.findByRole("button", { name: "Flatten (1)" }));
    expect(screen.getByRole("button", { name: "Confirm: close 1 position" })).toBeTruthy();
    act(() =>
      app.store.applyPositions("acc-a", {
        positions: [{ instrument_id: FUT, product: "MIS", qty: 300, avg_price_paise: "2500000", ltp_paise: 2501000, realised_paise: 0, unrealised_paise: 0, pnl_paise: 0, buy_qty: 300, sell_qty: 0, buy_value_paise: 0, sell_value_paise: 0, updated_at: "2026-10-14T04:41:00+00:00" }],
        realised_paise: 0, unrealised_paise: 0, day_pnl_paise: 0, reconcile: { checked_at: null, ok: null, error: "", alerts: [] },
      }),
    ); // a fill meanwhile: what was confirmed is not what is open now
    expect(screen.queryByRole("button", { name: /Confirm/ })).toBeNull();
    await screen.findByRole("button", { name: "Flatten (1)" });
    expect(posts(calls)).toHaveLength(0);
  });

  it("a position whose size isn't known is shown and never offered for flattening", async () => {
    const unknown = { ...STATUS, can_flatten: false, positions: [{ ...STATUS.positions[0]!, unknown: "MockTrade shows +150, the broker shows +225" }] };
    setup({ [`GET ${BASE}`]: unknown });
    await screen.findByText("NIFTY26OCTFUT MIS: size not known, close it at the broker");
    expect(screen.queryByRole("button", { name: /Flatten/ })).toBeNull();
  });

  it("refused or partly closed positions are errors in Messages", async () => {
    const partial = { positions: [{ instrument_id: FUT, orders: ["MT9"], refused: [], left_open: 75, closed: true, summary: "closing +150: sent MT9; 75 left to orders already closing it" }], summary: "" };
    const { app } = setup({ [`GET ${BASE}`]: STATUS, [`POST ${BASE}`]: partial });
    fireEvent.click(await screen.findByRole("button", { name: "Flatten (1)" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm: close 1 position" }));
    await vi.waitFor(() => expect(app.store.getState().messages[0]).toMatchObject({ level: "ERROR", text: "Flatten: closing +150: sent MT9; 75 left to orders already closing it" }));
  });

  it("without the server's report it still says trading is stopped and positions are kept", async () => {
    const { calls } = setup({});
    await vi.waitFor(() => expect(calls.some((c) => c.path === BASE)).toBe(true));
    expect(screen.getByText("TRADING STOPPED · acc-a")).toBeTruthy();
    expect(screen.getByText("feeling unsure · by ayush · new orders blocked · positions kept")).toBeTruthy();
  });
});
