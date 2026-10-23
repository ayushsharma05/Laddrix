// The session: logging in and out, and keeping the Store in step with the server.
//
//   boot() ─► GET /api/me ─┬─ 401 ─► login screen
//                          ├─ must_change_password ─► "set your password" (no stream yet)
//                          └─ ok  ─► start the stream
//   stream "hello" (first connect and every reconnect)
//          ─► GET /api/me, then orders + positions for every account ─► Store (merge by time)
//   stream closed 4401 ─► Store.setMe(null) ─► login screen

import { ApiError, type Rest } from "../api/rest";
import type { StreamClient } from "../api/stream";
import type { AlgoView, ChargeBill, GttView, Me, OrderView, PortfolioKind, PortfolioSnapshot, PositionsSnapshot } from "../api/types";
import type { Store } from "./store";

const KINDS: PortfolioKind[] = ["funds", "holdings", "trades"];
export const RETRY_MS = 3000; // a failed load is tried again (while connected)

export class Session {
  private unsubscribers: (() => void)[] = [];
  private loading: Promise<void> | null = null;
  private epoch = 0; // one per load: answers from an older load are dropped (#268)
  private retry: ReturnType<typeof setTimeout> | null = null;

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
      stream.on("hello", () => {
        this.epoch += 1; // a new connect: whatever an older load was doing is out of date
        this.loading = null;
        void this.reload();
      }),
      stream.on("order", (m) => store.applyMessage(m)),
      stream.on("positions", (m) => store.applyMessage(m)),
      stream.on("kill", (m) => store.applyMessage(m)),
      stream.on("position_alert", (m) => store.applyMessage(m)),
      stream.on("algo", (m) => store.applyMessage(m)),
      stream.on("gtt", (m) => store.applyMessage(m)),
      stream.on("portfolio", (m) => store.applyMessage(m)),
      stream.on("charges", (m) => store.applyMessage(m)),
      stream.on("mtm", (m) => store.applyMessage(m)),
      stream.on("market_alert", (m) => store.applyMessage(m)),
      stream.on("ops", (m) => store.applyMessage(m)),
      stream.on("error", (m) => store.applyMessage(m)),
    );
  }

  /** On page load: are we still logged in? */
  async boot(): Promise<boolean> {
    try {
      const me = await this.rest.get<Me>("/api/me");
      this.store.setMe(me);
      if (!me.user.must_change_password) this.stream.start(); // the server refuses it until then
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
    if (!me.user.must_change_password) this.stream.start(); // first their own password
  }

  /** The user's own new password (also the first step after an admin's reset). Then the
   *  terminal starts as after a sign-in. */
  async changePassword(old: string, password: string): Promise<void> {
    await this.rest.post("/api/me/password", { old, new: password });
    const me = await this.rest.get<Me>("/api/me");
    this.store.setMe(me);
    if (!me.user.must_change_password) this.stream.start();
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
    const epoch = this.epoch;
    const current = () => epoch === this.epoch; // false once a newer connect started a new load
    if (this.retry) clearTimeout(this.retry);
    this.retry = null;
    try {
      const me = await this.rest.get<Me>("/api/me");
      if (!current()) return;
      this.store.setMe(me);
      this.store.startSync(epoch, me.accounts.map((a) => a.id));
      await Promise.all(
        me.accounts.map(async (account) => {
          const base = `/api/accounts/${encodeURIComponent(account.id)}`;
          const missing: string[] = [];
          const optional = <T,>(path: string, what: string) =>
            this.rest.get<T>(path).catch((err: unknown) => {
              if (err instanceof ApiError && err.status === 404) return null; // not offered here
              if (err instanceof ApiError && err.loggedOut) throw err;
              missing.push(what); // unavailable, not empty: said, never shown as "none"
              return null;
            });
          const [orders, positions, algos, gtts] = await Promise.all([
            this.rest.get<OrderView[]>(`${base}/orders`),
            this.rest.get<PositionsSnapshot>(`${base}/positions`),
            optional<AlgoView[]>(`${base}/algos`, "algos"),
            optional<GttView[]>(`${base}/gtt`, "GTTs"),
          ]);
          if (!current()) return;
          this.store.applyOrders(account.id, orders);
          this.store.applyPositions(account.id, positions);
          if (algos) this.store.applyAlgos(account.id, algos);
          if (gtts) this.store.applyGtts(account.id, gtts);
          this.store.accountSynced(epoch, account.id, missing);
          await Promise.all(KINDS.map((kind) => this.loadPortfolio(account.id, kind)));
          const charges = await this.rest.get<ChargeBill>(`${base}/charges`).catch(() => null); // a nicety
          if (charges && current()) this.store.applyCharges(account.id, charges);
        }),
      );
    } catch (err) {
      if (err instanceof ApiError && err.loggedOut) {
        this.stream.stop();
        this.store.setMe(null);
        return;
      }
      if (!current()) return;
      const text = err instanceof Error ? err.message : String(err);
      this.store.syncFailed(epoch, text);
      this.store.addMessage("ERROR", "", `Couldn't load orders and positions: ${text} (trying again; cancels still go)`);
      this.retry = setTimeout(() => {
        this.retry = null;
        if (current() && this.stream.status === "open") void this.reload();
      }, RETRY_MS);
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
    if (this.retry) clearTimeout(this.retry);
    for (const off of this.unsubscribers) off();
    this.unsubscribers = [];
  }
}
