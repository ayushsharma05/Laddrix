import { fakeRest, fakeStream } from "../test/fakes";
import { Session } from "./session";
import { ME, order } from "../test/fixtures";
import { Rest } from "../api/rest";
import { cancelBlock, tradingBlock } from "./gate";
import { RETRY_MS } from "./session";
import { Store } from "./store";

const SNAP = { positions: [], realised_paise: 0, unrealised_paise: 0, day_pnl_paise: 0, reconcile: { checked_at: null, ok: true, error: "", alerts: [] } };

function setup(routes: Record<string, unknown> = {}) {
  const server = fakeRest({
    "GET /api/me": ME,
    "GET /api/accounts/acc-a/orders": [order("o1", "OPEN", "2026-10-14T04:45:00+00:00")],
    "GET /api/accounts/acc-a/positions": SNAP,
    "GET /api/accounts/acc-view/orders": [],
    "GET /api/accounts/acc-view/positions": SNAP,
    ...routes,
  });
  const { stream, socket } = fakeStream();
  const store = new Store();
  const session = new Session(server.rest, stream, store);
  return { ...server, stream, socket, store, session };
}

describe("Session", () => {
  it("boots into the login screen when there is no session", async () => {
    const { session, store, stream } = setup({ "GET /api/me": () => ({ status: 401, body: { detail: "please log in" } }) });
    await expect(session.boot()).resolves.toBe(false);
    expect(store.getState().me).toBeNull();
    expect(stream.status).toBe("stopped");
  });

  it("loads orders and positions for every account on hello", async () => {
    const { session, store, socket, calls } = setup();
    await session.boot();
    socket().open();
    socket().receive({ type: "hello", user: "ayush", accounts: ["acc-a"], flush_ms: 250, max_instruments: 200 });
    await session.reload(); // joins the load hello started
    expect(store.getState().books["acc-a"]!.orders.o1!.status).toBe("OPEN");
    expect(store.getState().books["acc-a"]!.reconcile!.ok).toBe(true);
    expect(calls.filter((c) => c.path === "/api/accounts/acc-a/orders")).toHaveLength(1);
  });

  it("applies stream events to the store", async () => {
    const { session, store, socket } = setup();
    await session.boot();
    socket().open();
    socket().receive({ type: "order", account_id: "acc-a", kind: "FILL", detail: "", order: order("o9", "FILLED", "2026-10-14T04:50:00+00:00"), fill: { client_order_id: "o9", qty: 75, price_paise: 2501000, exact: true } });
    expect(store.getState().books["acc-a"]!.orders.o9!.status).toBe("FILLED");
    expect(store.getState().messages[0]!.text).toBe("Bought 75 NIFTY26OCTFUT @ 25,010.00");
  });

  it("returns to the login screen when the stream says logged out", async () => {
    const { session, store, socket } = setup();
    await session.boot();
    socket().open();
    socket().drop(4401);
    expect(store.getState().me).toBeNull();
  });

  it("logs in and out", async () => {
    const { session, store, stream, calls } = setup({ "POST /api/session": ME, "DELETE /api/session": () => ({ status: 204 }) });
    await session.login("ayush", "pw", "123456");
    expect(calls[0]).toEqual({ method: "POST", path: "/api/session", body: { username: "ayush", password: "pw", code: "123456" } });
    expect(store.getState().me?.user.id).toBe("ayush");
    expect(stream.status).toBe("connecting");
    await session.logout();
    expect(store.getState().me).toBeNull();
    expect(stream.status).toBe("stopped");
  });

  it("reports a failed load in Messages instead of failing silently", async () => {
    const { session, store } = setup({ "GET /api/accounts/acc-a/orders": () => ({ status: 500, body: { detail: "db locked" } }) });
    await session.reload();
    expect(store.getState().messages[0]!.text).toBe("Couldn't load orders and positions: db locked (trying again; cancels still go)");
  });

  describe("trading waits for the server's state, not just an open stream (#268)", () => {
    // a server whose answers can be held back, to see the screens while a load is under way
    function slow(routes: Record<string, unknown>) {
      const held: (() => void)[] = [];
      const server = fakeRest(routes);
      let hold = false;
      const rest = new Rest(async (path, init) => {
        if (hold && path.endsWith("/orders")) await new Promise<void>((go) => held.push(go));
        return (server.rest as unknown as { fetcher: typeof fetch }).fetcher(path, init);
      });
      return { rest, held, routes: server.routes, holdOn: () => (hold = true), holdOff: () => (hold = false) };
    }

    function connected(routes: Record<string, unknown> = {}) {
      const base = {
        "GET /api/me": ME,
        "GET /api/accounts/acc-a/orders": [order("o1", "OPEN", "2026-10-14T04:45:00+00:00")],
        "GET /api/accounts/acc-a/positions": SNAP,
        "GET /api/accounts/acc-view/orders": [],
        "GET /api/accounts/acc-view/positions": SNAP,
        ...routes,
      };
      const s = slow(base);
      const { stream, socket } = fakeStream();
      const store = new Store();
      const session = new Session(s.rest, stream, store);
      return { ...s, stream, socket, store, session };
    }

    const hello = { type: "hello", user: "ayush", accounts: ["acc-a"], flush_ms: 250, max_instruments: 200 };

    it("an open stream with orders still loading takes no new orders; cancels go", async () => {
      const w = connected();
      await w.session.boot();
      w.holdOn();
      w.socket().open();
      w.socket().receive(hello);
      await vi.waitFor(() => expect(w.held.length).toBeGreaterThan(0));
      const s = w.store.getState();
      expect(s.stream).toBe("open");
      expect(tradingBlock(s, "acc-a")).toBe("loading acc-a's orders and positions: order clicks wait until they're in");
      expect(cancelBlock(s, "acc-a")).toBeNull(); // the server is the authority on a cancel
      for (const go of w.held) go();
      await w.session.reload();
      expect(tradingBlock(w.store.getState(), "acc-a")).toBeNull();
    });

    it("a failed load keeps new orders closed, says so, and tries again", async () => {
      vi.useFakeTimers();
      try {
        let fail = true;
        const w = connected({
          "GET /api/accounts/acc-a/positions": () => (fail ? { status: 503, body: { detail: "busy" } } : { status: 200, body: SNAP }),
        });
        await w.session.boot();
        w.socket().open();
        w.socket().receive(hello);
        await vi.waitFor(() => expect(w.store.getState().sync.accounts["acc-a"]).toBe("failed"));
        expect(tradingBlock(w.store.getState(), "acc-a")).toBe("couldn't load acc-a's orders and positions (busy): trying again; cancels still go");
        fail = false;
        await vi.advanceTimersByTimeAsync(RETRY_MS);
        await vi.waitFor(() => expect(w.store.getState().sync.accounts["acc-a"]).toBe("ready"));
        expect(tradingBlock(w.store.getState(), "acc-a")).toBeNull();
      } finally {
        vi.useRealTimers();
      }
    });

    it("answers from an older connect are dropped", async () => {
      const w = connected();
      await w.session.boot();
      w.holdOn();
      w.socket().open();
      w.socket().receive(hello); // load 1: its orders are held back
      await vi.waitFor(() => expect(w.held.length).toBe(2));
      w.holdOff();
      w.socket().receive(hello); // a reconnect: load 2 starts and finishes
      await w.session.reload();
      const epoch = w.store.getState().sync.epoch;
      // load 1's answers arrive late, saying something newer-looking that load 2 didn't see
      w.routes["GET /api/accounts/acc-a/orders"] = [order("o1", "CANCELLED", "2026-10-14T04:50:00+00:00")];
      for (const go of w.held) go();
      await new Promise((r) => setTimeout(r, 0));
      expect(w.store.getState().sync.epoch).toBe(epoch);
      expect(w.store.getState().books["acc-a"]!.orders.o1!.status).toBe("OPEN"); // dropped
    });

    it("algos or GTTs that couldn't be loaded are unavailable, not empty", async () => {
      const w = connected({ "GET /api/accounts/acc-a/algos": () => ({ status: 500, body: { detail: "boom" } }) });
      await w.session.boot();
      w.socket().open();
      w.socket().receive(hello);
      await w.session.reload();
      expect(w.store.getState().sync.missing["acc-a"]).toEqual(["algos"]);
      expect(w.store.getState().sync.missing["acc-view"]).toEqual([]); // 404: not offered, fine
    });
  });
});
