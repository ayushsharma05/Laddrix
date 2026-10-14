// Everything the screens show that isn't a price: who is logged in, orders, positions,
// kill switches, position alerts and the Messages list.
//
//   REST snapshots ─┐
//                   ├─► Store.apply…() ─► new state object ─► widgets re-render (useStore)
//   stream events ──┘
//
// State is never changed in place: every change makes a new object, so React can tell what
// changed by comparing references. Orders and positions keep whichever version is newest
// by `updated_at`, so a snapshot that arrives after a newer stream event can't undo it
// (docs/design/stream.md, "After every (re)connect").

import type { Status } from "../api/stream";
import type { AlgoView, Me, OrderView, Position, PositionsSnapshot, ServerMessage, StrategyLegIn } from "../api/types";
import { price, qty, shortName } from "../lib/format";

export type Level = "ERROR" | "WARN" | "INFO" | "OK";

export interface Message {
  id: number;
  at: number; // ms since epoch
  level: Level;
  instrumentId: string;
  text: string;
}

export interface KillState {
  engaged: boolean;
  reason: string;
  by: string;
}

export interface PositionAlert {
  instrumentId: string;
  ours: number;
  broker: number;
  message: string;
}

export interface AccountBook {
  orders: Record<string, OrderView>; // by client_order_id
  positions: Record<string, Position>; // by instrument_id + product
  dayPnl: number;
  reconcile: PositionsSnapshot["reconcile"] | null;
  alerts: Record<string, PositionAlert>; // by instrument_id
  kill: KillState;
  algos: Record<string, AlgoView>; // by algo_id
}

export interface State {
  me: Me | null;
  accountId: string | null; // the account new orders go to
  books: Record<string, AccountBook>;
  firmKill: KillState;
  /** Operational alerts from the server (Kite login, prices stopped, disk), active ones by key. */
  ops: Record<string, OpsAlert>;
  /** Legs sent from the option chain (Alt-click) waiting for the strategy builder. */
  strategyInbox: StrategyLegIn[];
  messages: Message[]; // newest first
  stream: Status;
  sentTimes: number[]; // when we sent orders in the last minute (rate meter)
}

export interface OpsAlert {
  key: string;
  level: "INFO" | "WARN" | "ERROR";
  text: string;
  at: string;
}

export const MAX_MESSAGES = 500;
const OFF: KillState = { engaged: false, reason: "", by: "" };

export function emptyBook(): AccountBook {
  return { orders: {}, positions: {}, dayPnl: 0, reconcile: null, alerts: {}, kill: OFF, algos: {} };
}

export function positionKey(p: { instrument_id: string; product: string }): string {
  return `${p.instrument_id}|${p.product}`;
}

/** True when `a` is at least as new as `b` (ISO times; missing counts as oldest). */
function newer(a: string | null | undefined, b: string | null | undefined): boolean {
  if (!b) return true;
  if (!a) return false;
  return Date.parse(a) >= Date.parse(b);
}

export class Store {
  private state: State;
  private listeners = new Set<() => void>();
  private nextMessageId = 1;

  constructor(private readonly now: () => number = Date.now) {
    this.state = {
      me: null,
      accountId: null,
      books: {},
      firmKill: OFF,
      ops: {},
      strategyInbox: [],
      messages: [],
      stream: "stopped",
      sentTimes: [],
    };
  }

  getState = (): State => this.state;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  private set(next: Partial<State>): void {
    this.state = { ...this.state, ...next };
    for (const listener of this.listeners) listener();
  }

  private book(accountId: string): AccountBook {
    return this.state.books[accountId] ?? emptyBook();
  }

  private setBook(accountId: string, change: Partial<AccountBook>): void {
    this.set({ books: { ...this.state.books, [accountId]: { ...this.book(accountId), ...change } } });
  }

  // --- session ------------------------------------------------------------------------------

  setMe(me: Me | null): void {
    if (me === null) {
      this.set({ me: null, accountId: null, books: {}, firmKill: OFF, ops: {}, sentTimes: [] });
      return;
    }
    const books: Record<string, AccountBook> = {};
    for (const account of me.accounts) {
      books[account.id] = {
        ...this.book(account.id),
        kill: account.stopped ? { engaged: true, reason: account.stop_reason, by: "" } : OFF,
      };
    }
    const keep = me.accounts.some((a) => a.id === this.state.accountId);
    const tradable = me.accounts.find((a) => a.can_trade) ?? me.accounts[0];
    this.set({
      me,
      books,
      accountId: keep ? this.state.accountId : (tradable?.id ?? null),
      firmKill: me.firm_stopped ? { engaged: true, reason: this.state.firmKill.reason, by: "" } : OFF,
    });
  }

  selectAccount(accountId: string): void {
    if (this.state.me?.accounts.some((a) => a.id === accountId)) this.set({ accountId });
  }

  setStream(status: Status): void {
    if (status !== this.state.stream) this.set({ stream: status });
  }

  // --- snapshots ------------------------------------------------------------------------------

  applyOrders(accountId: string, orders: OrderView[]): void {
    const merged = { ...this.book(accountId).orders };
    for (const order of orders) {
      const id = order.request.client_order_id;
      if (newer(order.updated_at, merged[id]?.updated_at)) merged[id] = order;
    }
    this.setBook(accountId, { orders: merged });
  }

  applyPositions(accountId: string, snap: PositionsSnapshot): void {
    const merged = { ...this.book(accountId).positions };
    for (const p of snap.positions) {
      const key = positionKey(p);
      if (newer(p.updated_at, merged[key]?.updated_at)) merged[key] = p;
    }
    const alerts: Record<string, PositionAlert> = {};
    for (const a of snap.reconcile.alerts) {
      alerts[a.instrument_id] = {
        instrumentId: a.instrument_id,
        ours: a.ours,
        broker: a.broker,
        message: a.message,
      };
    }
    this.setBook(accountId, {
      positions: merged,
      dayPnl: snap.day_pnl_paise,
      reconcile: snap.reconcile,
      alerts,
    });
  }

  applyAlgos(accountId: string, algos: AlgoView[]): void {
    const merged = { ...this.book(accountId).algos };
    for (const a of algos) {
      if (newer(a.updated_at, merged[a.algo_id]?.updated_at)) merged[a.algo_id] = a;
    }
    this.setBook(accountId, { algos: merged });
  }

  // --- the strategy builder's inbox -----
  private strategyBuilders = 0;

  sendToStrategy(leg: StrategyLegIn): void {
    this.set({ strategyInbox: [...this.state.strategyInbox, leg] });
  }

  takeStrategyInbox(): StrategyLegIn[] {
    const legs = this.state.strategyInbox;
    if (legs.length) this.set({ strategyInbox: [] });
    return legs;
  }

  /** A builder is open (the chain opens one when none is). Returns the unregister. */
  registerStrategyBuilder(): () => void {
    this.strategyBuilders += 1;
    return () => {
      this.strategyBuilders -= 1;
    };
  }

  get hasStrategyBuilder(): boolean {
    return this.strategyBuilders > 0;
  }

  // --- the stream -----------------------------------------------------------------------------

  applyMessage(m: ServerMessage): void {
    switch (m.type) {
      case "order":
        return this.applyOrderEvent(m);
      case "positions": {
        const merged = { ...this.book(m.account_id).positions };
        for (const p of m.positions) {
          const key = positionKey(p);
          if (newer(p.updated_at, merged[key]?.updated_at)) merged[key] = p;
        }
        return this.setBook(m.account_id, { positions: merged, dayPnl: m.day_pnl_paise });
      }
      case "kill": {
        const kill = m.engaged ? { engaged: true, reason: m.reason, by: m.by } : OFF;
        const what = m.scope === "firm" ? "the whole firm" : m.scope; // "account acc-x"
        if (m.engaged) {
          this.addMessage("ERROR", "", `Trading stopped for ${what} by ${m.by}: ${m.reason}`);
        } else {
          this.addMessage("OK", "", `Trading resumed for ${what} by ${m.by}`);
        }
        if (m.scope === "firm") return this.set({ firmKill: kill });
        const accountId = m.scope.replace(/^account /, "");
        return this.setBook(accountId, { kill });
      }
      case "position_alert": {
        const alerts = { ...this.book(m.account_id).alerts };
        if (m.state === "raised") {
          alerts[m.instrument_id] = {
            instrumentId: m.instrument_id,
            ours: m.ours,
            broker: m.broker,
            message: m.message,
          };
          this.addMessage("WARN", m.instrument_id, m.message);
        } else {
          delete alerts[m.instrument_id];
          this.addMessage("OK", m.instrument_id, `${shortName(m.instrument_id)}: positions match the broker again`);
        }
        return this.setBook(m.account_id, { alerts });
      }
      case "algo": {
        this.applyAlgos(m.account_id, [m.algo]);
        const note = describeAlgoEvent(m.kind, m.detail, m.algo);
        if (note) this.addMessage(note.level, m.algo.instrument_id, note.text);
        return;
      }
      case "ops": {
        const ops = { ...this.state.ops };
        if (m.active && m.level !== "OK") ops[m.key] = { key: m.key, level: m.level, text: m.text, at: m.at };
        else delete ops[m.key];
        this.set({ ops });
        this.addMessage(m.level === "OK" ? "OK" : m.level, "", m.text);
        return;
      }
      case "error":
        this.addMessage("ERROR", "", m.message);
        return;
      default:
        return;
    }
  }

  private applyOrderEvent(m: Extract<ServerMessage, { type: "order" }>): void {
    const order = m.order;
    if (order) {
      const id = order.request.client_order_id;
      const current = this.book(m.account_id).orders[id];
      if (newer(order.updated_at, current?.updated_at)) {
        this.setBook(m.account_id, { orders: { ...this.book(m.account_id).orders, [id]: order } });
      }
    }
    const note = describeOrderEvent(m.kind, m.detail, order, m.fill);
    if (note) this.addMessage(note.level, order?.request.instrument_id ?? "", note.text);
  }

  // --- messages and the rate meter -------------------------------------------------------------

  addMessage(level: Level, instrumentId: string, text: string): Message {
    const message = { id: this.nextMessageId++, at: this.now(), level, instrumentId, text };
    this.set({ messages: [message, ...this.state.messages].slice(0, MAX_MESSAGES) });
    return message;
  }

  clearMessages(): void {
    this.set({ messages: [] });
  }

  noteSent(): void {
    const now = this.now();
    this.set({ sentTimes: [...this.state.sentTimes.filter((t) => t > now - 60_000), now] });
  }

  sentLastMinute(): number {
    const now = this.now();
    return this.state.sentTimes.filter((t) => t > now - 60_000).length;
  }
}

/** The Messages line for an order event, or null for routine steps that need no line. */
export function describeOrderEvent(
  kind: string,
  detail: string,
  order: OrderView | null,
  fill: { qty: number; price_paise: number; exact: boolean } | null,
): { level: Level; text: string } | null {
  const r = order?.request;
  const what = r ? `${r.side} ${qty(r.qty)} ${shortName(r.instrument_id)}` : "Order";
  const reason = detail || order?.reason || "";
  switch (kind) {
    case "REJECTED":
      return { level: "ERROR", text: `${what} refused: ${reason || "no reason given"}` };
    case "FILL":
      if (!fill || !r) return null;
      return {
        level: "OK",
        text: `${r.side === "BUY" ? "Bought" : "Sold"} ${qty(fill.qty)} ${shortName(r.instrument_id)} @ ${price(fill.price_paise)}${fill.exact ? "" : " (average)"}`,
      };
    case "UNCONFIRMED":
      return {
        level: "WARN",
        text: `${what}: the broker didn't answer; checking its order book before anything else is sent${reason ? ` (${reason})` : ""}`,
      };
    case "CANCELLED_LOCALLY":
      return { level: "WARN", text: `${what} cancelled here: ${reason || "never reached the broker"}` };
    case "EXTERNAL":
      return { level: "INFO", text: `${what}: changed outside MockTrade${reason ? ` (${reason})` : ""}` };
    case "UPDATED":
      if (order?.status === "CANCELLED") return { level: "INFO", text: `${what} cancelled` };
      if (order?.status === "REJECTED") {
        return { level: "ERROR", text: `${what} rejected by the broker: ${reason || "no reason given"}` };
      }
      return null;
    default:
      return null; // SUBMITTED, SENT, MODIFY_SENT, CANCEL_SENT: the order book shows these
  }
}

/** The Messages line for an algo event, or null for routine updates. */
export function describeAlgoEvent(kind: string, detail: string, a: AlgoView): { level: Level; text: string } | null {
  const what = `${a.algo_id} ${a.code} ${a.side} ${qty(a.qty)} ${shortName(a.instrument_id)}`;
  switch (kind) {
    case "STARTED":
      return { level: "INFO", text: `${what} started` };
    case "ALERT":
      return { level: "ERROR", text: `${what}: ${detail}` };
    case "DONE":
      return { level: "OK", text: `${what} done: ${qty(a.filled_qty)} filled @ ${price(a.avg_price_paise)}` };
    case "STOPPED":
      return { level: "INFO", text: `${what} stopped: ${a.reason}` };
    case "FAILED":
      return { level: "ERROR", text: `${what} failed: ${a.reason}` };
    default:
      return null; // UPDATED, PAUSED, RESUMED, STOPPING: the algo manager shows these
  }
}
