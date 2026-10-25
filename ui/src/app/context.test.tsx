// The data hooks never hand a screen another contract's values, not even for the one render
// between a switch and the effect that loads the new one (a ticket would start at the wrong price).
import { act, render } from "@testing-library/react";
import { fakeRest, fakeStream, tick } from "../test/fakes";
import { ME, orderSpec } from "../test/fixtures";
import { AppProvider, createApp, useInstrument, useOrderSpec, useTick } from "./context";

const A = "NFO:NIFTY26OCTFUT";
const B = "NFO:NIFTY26NOVFUT";
const enc = encodeURIComponent;

function world() {
  const server = fakeRest({
    [`GET /api/accounts/acc-a/instruments/${enc(A)}/order-spec`]: orderSpec(A),
    [`GET /api/instruments/${enc(A)}`]: { id: A, exchange: "NFO", symbol: "NIFTY26OCTFUT", kind: "FUT", tick_paise: 10, lot_size: 75, expiry: "2026-10-27", strike_paise: null, underlying: "NIFTY", tradable: true, ltp_paise: 2501000 },
  });
  const { stream, socket } = fakeStream();
  const app = createApp({ rest: server.rest, stream });
  app.store.setMe(ME);
  stream.start();
  socket().open();
  return { app, socket };
}

describe("data hooks after a switch of contract", () => {
  it("useTick: never the previous contract's tick", async () => {
    const { app, socket } = world();
    const seen: (string | null)[] = [];
    function Probe({ id }: { id: string }) {
      const t = useTick(id);
      seen.push(t ? t.instrument_id : null);
      return null;
    }
    const view = render(<AppProvider app={app}><Probe id={A} /></AppProvider>);
    act(() => socket().receive({ type: "ticks", ticks: [tick(A, 2501000)] }));
    expect(seen.at(-1)).toBe(A);
    seen.length = 0;
    view.rerender(<AppProvider app={app}><Probe id={B} /></AppProvider>);
    expect(seen.length).toBeGreaterThan(0);
    expect(seen.every((x) => x === null)).toBe(true); // B has no price yet: nothing, not A's
    act(() => socket().receive({ type: "ticks", ticks: [tick(B, 2514800)] }));
    expect(seen.at(-1)).toBe(B);
  });

  it("useTick: switching back never shows the old price (A, then B, then A again)", async () => {
    const { app, socket } = world();
    const seen: (number | null)[] = [];
    function Probe({ id }: { id: string }) {
      const t = useTick(id);
      seen.push(t ? t.ltp_paise : null);
      return null;
    }
    const view = render(<AppProvider app={app}><Probe id={A} /></AppProvider>);
    act(() => socket().receive({ type: "ticks", ticks: [tick(A, 2501000)] }));
    view.rerender(<AppProvider app={app}><Probe id={B} /></AppProvider>);
    seen.length = 0;
    view.rerender(<AppProvider app={app}><Probe id={A} /></AppProvider>);
    expect(seen.length).toBeGreaterThan(0);
    expect(seen.every((x) => x === null)).toBe(true); // A's 25,010.00 from before is stale now
  });

  it("useInstrument: never the previous contract while another one loads", async () => {
    const { app } = world();
    const seen: (string | null)[] = [];
    function Probe({ id }: { id: string }) {
      const i = useInstrument(id);
      seen.push(i ? i.id : null);
      return null;
    }
    const view = render(<AppProvider app={app}><Probe id={A} /></AppProvider>);
    await vi.waitFor(() => expect(seen.at(-1)).toBe(A));
    seen.length = 0;
    view.rerender(<AppProvider app={app}><Probe id={B} /></AppProvider>);
    await act(async () => {}); // B is unknown to the server (404): it stays unknown
    expect(seen.length).toBeGreaterThan(0);
    expect(seen.every((x) => x === null)).toBe(true);
  });

  it("useOrderSpec: never the previous contract's rules", async () => {
    const { app } = world();
    const seen: (string | null)[] = [];
    function Probe({ id }: { id: string }) {
      const s = useOrderSpec("acc-a", id);
      seen.push(s ? s.instrument_id : null);
      return null;
    }
    const view = render(<AppProvider app={app}><Probe id={A} /></AppProvider>);
    await vi.waitFor(() => expect(seen.at(-1)).toBe(A));
    seen.length = 0;
    view.rerender(<AppProvider app={app}><Probe id={B} /></AppProvider>);
    await act(async () => {}); // B's spec is refused (404): it stays unknown
    expect(seen.length).toBeGreaterThan(0);
    expect(seen.every((x) => x === null)).toBe(true);
  });
});
