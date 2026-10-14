// The session: logging in and out, and keeping the Store in step with the server.
//
//   boot() ─► GET /api/me ─┬─ 401 ─► login screen
//                          └─ ok  ─► start the stream
//   stream "hello" (first connect and every reconnect)
//          ─► GET /api/me, then orders + positions for every account ─► Store (merge by time)
//   stream closed 4401 ─► Store.setMe(null) ─► login screen

import { ApiError, type Rest } from "../api/rest";
import type { StreamClient } from "../api/stream";
import type { Me, OrderView, PositionsSnapshot } from "../api/types";
import type { Store } from "./store";

export class Session {
  private unsubscribers: (() => void)[] = [];
  private loading: Promise<void> | null = null;

  constructor(
    private readonly rest: Rest,
    private readonly stream: StreamClient,
    private readonly store: Store,
  ) {
    this.unsubscribers.push(
      stream.onStatus((status) => {
        store.setStream(status);
        if (status === "logged-out") store.setMe(null);
      }),
      stream.on("hello", () => void this.reload()),
      stream.on("order", (m) => store.applyMessage(m)),
      stream.on("positions", (m) => store.applyMessage(m)),
      stream.on("kill", (m) => store.applyMessage(m)),
      stream.on("position_alert", (m) => store.applyMessage(m)),
      stream.on("error", (m) => store.applyMessage(m)),
    );
  }

  /** On page load: are we still logged in? */
  async boot(): Promise<boolean> {
    try {
      const me = await this.rest.get<Me>("/api/me");
      this.store.setMe(me);
      this.stream.start();
      return true;
    } catch (err) {
      if (err instanceof ApiError && err.loggedOut) {
        this.store.setMe(null);
        return false;
      }
      throw err;
    }
  }

  async login(username: string, password: string, code: string): Promise<void> {
    const me = await this.rest.post<Me>("/api/session", { username, password, code });
    this.store.setMe(me);
    this.stream.start();
  }

  async logout(): Promise<void> {
    this.stream.stop();
    try {
      await this.rest.delete("/api/session");
    } finally {
      this.store.setMe(null);
    }
  }

  /** Load the current state after a (re)connect. Concurrent calls share one load. */
  reload(): Promise<void> {
    this.loading ??= this.load().finally(() => {
      this.loading = null;
    });
    return this.loading;
  }

  private async load(): Promise<void> {
    try {
      const me = await this.rest.get<Me>("/api/me");
      this.store.setMe(me);
      await Promise.all(
        me.accounts.map(async (account) => {
          const base = `/api/accounts/${encodeURIComponent(account.id)}`;
          const [orders, positions] = await Promise.all([
            this.rest.get<OrderView[]>(`${base}/orders`),
            this.rest.get<PositionsSnapshot>(`${base}/positions`),
          ]);
          this.store.applyOrders(account.id, orders);
          this.store.applyPositions(account.id, positions);
        }),
      );
    } catch (err) {
      if (err instanceof ApiError && err.loggedOut) {
        this.stream.stop();
        this.store.setMe(null);
        return;
      }
      const text = err instanceof Error ? err.message : String(err);
      this.store.addMessage("ERROR", "", `Couldn't load orders and positions: ${text}`);
    }
  }

  dispose(): void {
    for (const off of this.unsubscribers) off();
    this.unsubscribers = [];
  }
}
