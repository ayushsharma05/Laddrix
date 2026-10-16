import { act, fireEvent, render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import type { MtmRules, Position, SquareOffStatus } from "../../api/types";
import { AppProvider, createApp } from "../../app/context";
import { fakeRest, fakeStream } from "../../test/fakes";
import { ME } from "../../test/fixtures";
import { SquareOffButton } from "../books/SquareOff";
import { MtmLimits, ruleText } from "./MtmLimits";

const FUT = "NFO:NIFTY26OCTFUT";
const POSITION: Position = {
  instrument_id: FUT,
  product: "MIS",
  qty: 75,
  avg_price_paise: "2501010",
  ltp_paise: 2499000,
  realised_paise: 0,
  unrealised_paise: -150750,
  pnl_paise: -150750,
  buy_qty: 75,
  sell_qty: 0,
  buy_value_paise: 187575750,
  sell_value_paise: 0,
  updated_at: "2026-10-16T09:35:00+00:00",
} as Position;
const STATUS: SquareOffStatus = {
  account_id: "acc-a",
  now: "2026-10-16T15:05:00+05:30",
  warn_from: "15:00",
  cutoffs: { NFO: "15:25", NSE: "15:20" },
  window_open: true,
  positions: [{ instrument_id: FUT, qty: 75, cutoff: "15:25", minutes_left: 20, pnl_paise: -150750 }],
};
const RULES: MtmRules = { positions: [], portfolio: null };

function setup(ui: ReactElement, routes: Record<string, unknown> = {}) {
  const server = fakeRest({
    "GET /api/accounts/acc-a/square-off": STATUS,
    "POST /api/accounts/acc-a/square-off": { closed: [{ instrument_id: FUT, qty: 75, orders: ["MT9"], refused: [] }] },
    "GET /api/accounts/acc-a/mtm": RULES,
    ...routes,
  });
  const { stream, socket } = fakeStream();
  const app = createApp({ rest: server.rest, stream });
  app.store.setMe(ME);
  app.store.selectAccount("acc-a");
  stream.start();
  socket().open();
  act(() => app.store.applyPositions("acc-a", { positions: [POSITION], day_pnl_paise: -150750, reconcile: { checked_at: null, ok: true, error: "", alerts: [] } } as never));
  render(<AppProvider app={app}>{ui}</AppProvider>);
  return { ...server, app, socket };
}

describe("square-off assistant", () => {
  it("shows the open MIS positions and closes them after one confirmation", async () => {
    const { calls, app } = setup(<SquareOffButton />);
    const button = await screen.findByRole("button", { name: "Square off MIS (1) · 15:25" });
    expect(button.className).toContain("danger"); // the window is open
    expect(button.getAttribute("title")).toContain("(in 20 min)");
    fireEvent.click(button);
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0); // one click only arms it
    fireEvent.click(screen.getByRole("button", { name: "Confirm: close 1 MIS" }));
    await vi.waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ confirm: true }));
    await vi.waitFor(() => expect(app.store.getState().messages[0]!.text).toBe(`Square-off ${FUT}: sent MT9`));
  });

  it("says in the server's words what the close did, including what it left to other closes", async () => {
    const summary = `closing +75 ${FUT} MIS: sent MT9; cancelled MT4; 0 left`;
    const { app } = setup(<SquareOffButton />, {
      "POST /api/accounts/acc-a/square-off": { closed: [{ instrument_id: FUT, qty: 75, orders: ["MT9"], refused: [], summary }] },
    });
    fireEvent.click(await screen.findByRole("button", { name: "Square off MIS (1) · 15:25" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm: close 1 MIS" }));
    await vi.waitFor(() => expect(app.store.getState().messages[0]!.text).toBe(`Square-off: ${summary}`));
  });

  it("shows nothing when no MIS position is open", async () => {
    const { calls } = setup(<SquareOffButton />, { "GET /api/accounts/acc-a/square-off": { ...STATUS, positions: [] } });
    await vi.waitFor(() => expect(calls.some((c) => c.path.endsWith("/square-off"))).toBe(true));
    expect(screen.queryByRole("button", { name: /Square off/ })).toBeNull();
  });
});

describe("MTM limits", () => {
  it("sets a position limit the server keeps, and shows it", async () => {
    const saved: MtmRules = {
      positions: [{ key: `${FUT}|MIS`, instrument_id: FUT, product: "MIS", max_loss_paise: 150000, max_loss_pct: null, auto_close: true, fired_today: false }],
      portfolio: null,
    };
    const { calls } = setup(<MtmLimits params={{ link: "none" }} setParams={vi.fn()} setInstrument={vi.fn()} publish={vi.fn()} open={vi.fn()} />, {
      "PUT /api/accounts/acc-a/mtm/positions": saved,
    });
    fireEvent.change(await screen.findByLabelText("NIFTY26OCTFUT loss limit"), { target: { value: "1,500" } });
    fireEvent.click(screen.getByLabelText("NIFTY26OCTFUT auto-close"));
    fireEvent.click(screen.getAllByRole("button", { name: "Set" })[1]!);
    await vi.waitFor(() =>
      expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ instrument_id: FUT, product: "MIS", max_loss_paise: 150000, max_loss_pct: null, auto_close: true }),
    );
    expect(await screen.findByText("loss ₹1,500.00 · auto-close")).toBeTruthy();
  });

  it("says what the server refused", async () => {
    setup(<MtmLimits params={{ link: "none" }} setParams={vi.fn()} setInstrument={vi.fn()} publish={vi.fn()} open={vi.fn()} />, {
      "PUT /api/accounts/acc-a/mtm/portfolio": () => ({ status: 422, body: { detail: "give a loss stop or a profit target" } }),
    });
    fireEvent.click((await screen.findAllByRole("button", { name: "Set" }))[0]!);
    expect((await screen.findByRole("alert")).textContent).toContain("give a loss stop or a profit target");
  });

  it("an MTM event from the server reaches Messages", () => {
    const { socket, app } = setup(<div />);
    act(() => socket().receive({ type: "mtm", account_id: "acc-a", kind: "position", key: `${FUT}|MIS`, text: "NIFTY: loss reached; closing", closing: true }));
    expect(app.store.getState().messages[0]).toMatchObject({ level: "ERROR", instrumentId: FUT, text: "MTM NIFTY: loss reached; closing" });
    expect(ruleText({ max_loss_paise: null, target_paise: 500000, auto_close: false, fired_today: true })).toBe("target ₹5,000.00 · alert only · reached today");
  });

  it("shows each limit reached in the server's words, retries only a blocked close after a confirmation, and says what is unprotected", async () => {
    const breach = (state: string, detail: string) => ({ id: `2026-10-16|${FUT}|MIS`, key: `${FUT}|MIS`, kind: "position", text: "loss reached", state, detail, attempts: 1, at: "" });
    const blocked = { ...RULES, breaches: [breach("BLOCKED", `${FUT} MIS +150 still open: refused (MT2: price band); retry when ready`)], problem: "rules: 1 saved MTM limit(s) can't be used and are NOT watched" };
    const { calls } = setup(<MtmLimits params={{ link: "none" }} setParams={vi.fn()} setInstrument={vi.fn()} publish={vi.fn()} open={vi.fn()} />, {
      "GET /api/accounts/acc-a/mtm": blocked,
      "POST /api/accounts/acc-a/mtm/retry": { ...RULES, breaches: [breach("REQUESTED", "closing")] },
    });
    expect(await screen.findByText(`${FUT} MIS +150 still open: refused (MT2: price band); retry when ready`)).toBeTruthy();
    expect(screen.getByText("blocked").className).toContain("tone-bad");
    expect(screen.getAllByRole("alert").map((a) => a.textContent)).toContain("rules: 1 saved MTM limit(s) can't be used and are NOT watched");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0); // one click only arms it
    fireEvent.click(screen.getByRole("button", { name: "Confirm: close again" }));
    await vi.waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ key: `${FUT}|MIS`, confirm: true }));
    expect(await screen.findByText("requested")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull(); // nothing to retry while it closes
  });
});
