// The session: logging in and out, and keeping the Store in step with the server.
//
//   boot() ─► GET /api/me ─┬─ 401 ─► login screen
//                          └─ ok  ─► start the stream
//   stream "hello" (first connect and every reconnect)
//          ─► GET /api/me, then orders + positions for every account ─► Store (merge by time)
//   stream closed 4401 ─► Store.setMe(null) ─► login screen

import { ApiError, type Rest } from "../api/rest";
import type { StreamClient } from "../api/stream";
import type { AlgoView, ChargeBill, GttView, Me, OrderView, PortfolioKind, PortfolioSnapshot, PositionsSnapshot } from "../api/types";
import type { Store } from "./store";

const KINDS: PortfolioKind[] = ["funds", "holdings", "trades"];

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
      stream.on("algo", (m) => store.applyMessage(m)),
      stream.on("gtt", (m) => store.applyMessage(m)),
      stream.on("portfolio", (m) => store.applyMessage(m)),
      stream.on("charges", (m) => store.applyMessage(m)),
      stream.on("mtm", (m) => store.applyMessage(m)),
      stream.on("ops", (m) => store.applyMessage(m)),
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
          const [orders, positions, algos, gtts] = await Promise.all([
            this.rest.get<OrderView[]>(`${base}/orders`),
            this.rest.get<PositionsSnapshot>(`${base}/positions`),
            this.rest.get<AlgoView[]>(`${base}/algos`).catch(() => [] as AlgoView[]), // a nicety
            this.rest.get<GttView[]>(`${base}/gtt`).catch(() => [] as GttView[]), // not every broker
          ]);
          this.store.applyOrders(account.id, orders);
          this.store.applyPositions(account.id, positions);
          this.store.applyAlgos(account.id, algos);
          this.store.applyGtts(account.id, gtts);
          await Promise.all(KINDS.map((kind) => this.loadPortfolio(account.id, kind)));
          const charges = await this.rest.get<ChargeBill>(`${base}/charges`).catch(() => null); // a nicety
          if (charges) this.store.applyCharges(account.id, charges);
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

  /** Funds, holdings or trades; `fresh` asks the broker now instead of the server's last read. */
  async loadPortfolio(accountId: string, kind: PortfolioKind, fresh = false): Promise<void> {
    const path = `/api/accounts/${encodeURIComponent(accountId)}/${kind}${fresh ? "?fresh=true" : ""}`;
    try {
      this.store.applyPortfolio(await this.rest.get<PortfolioSnapshot>(path));
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        this.store.portfolioMissing(accountId, kind, err.message);
        return;
      }
      if (err instanceof ApiError && err.loggedOut) throw err;
      const text = err instanceof Error ? err.message : String(err);
      this.store.addMessage("WARN", "", `Couldn't read ${kind}: ${text}`);
    }
  }

  dispose(): void {
    for (const off of this.unsubscribers) off();
    this.unsubscribers = [];
  }
}
