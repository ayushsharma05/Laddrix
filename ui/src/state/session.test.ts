import { fakeRest, fakeStream } from "../test/fakes";
import { Session } from "./session";
import { ME, order } from "../test/fixtures";
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
    expect(store.getState().messages[0]!.text).toBe("Couldn't load orders and positions: db locked");
  });
});
