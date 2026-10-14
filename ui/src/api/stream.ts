// The one WebSocket per tab (docs/design/stream.md).
//
//   widgets ─► subscribeTicks(id) ─► reference count ─► {"op":"subscribe"} only for the first
//   server  ─► messages ─► listeners by type; ticks ─► listeners per instrument
//
// - Two widgets watching one instrument share one subscription; the last one to leave
//   unsubscribes.
// - Drops reconnect with backoff (0.5 s → 8 s) and re-subscribe everything; listeners hear
//   "open" again so screens reload their snapshots (nothing is silently missing).
// - Close 4401 (logged out) stops for good: the app shows the login screen.

import type { OptionChain, ServerMessage, Tick } from "./types";

export interface ChainRequest {
  underlying: string;
  expiry: string;
  strikes: number;
}

export type Status = "connecting" | "open" | "reconnecting" | "logged-out" | "stopped";

export interface SocketLike {
  readyState: number;
  onopen: ((ev: Event) => void) | null;
  onmessage: ((ev: MessageEvent) => void) | null;
  onclose: ((ev: CloseEvent) => void) | null;
  onerror: ((ev: Event) => void) | null;
  send(data: string): void;
  close(code?: number): void;
}

type MessageOf<K extends ServerMessage["type"]> = Extract<ServerMessage, { type: K }>;
type Timer = ReturnType<typeof setTimeout>;

export const CLOSE_NOT_LOGGED_IN = 4401;
const OPEN = 1;
const BACKOFF_MS = [500, 1000, 2000, 4000, 8000];

export interface StreamOptions {
  url: string;
  createSocket?: (url: string) => SocketLike;
  setTimer?: (fn: () => void, ms: number) => Timer;
  clearTimer?: (t: Timer) => void;
}

export class StreamClient {
  private socket: SocketLike | null = null;
  private refs = new Map<string, number>(); // instrument -> how many widgets watch it
  private tickListeners = new Map<string, Set<(t: Tick) => void>>();
  private listeners = new Map<string, Set<(m: ServerMessage) => void>>();
  private statusListeners = new Set<(s: Status) => void>();
  private latest = new Map<string, Tick>(); // newest tick per watched instrument
  private chains = new Map<string, { req: ChainRequest; listeners: Set<(c: OptionChain) => void> }>();
  private attempt = 0;
  private timer: Timer | null = null;
  private wanted = false;
  status: Status = "stopped";
  /** When the last tick arrived (ms since epoch), for the "prices old" warning. */
  lastTickAt: number | null = null;

  constructor(private readonly opts: StreamOptions) {}

  start(): void {
    if (this.wanted) return;
    this.wanted = true;
    this.connect("connecting");
  }

  stop(): void {
    this.wanted = false;
    if (this.timer) (this.opts.clearTimer ?? clearTimeout)(this.timer);
    this.timer = null;
    this.socket?.close(1000);
    this.socket = null;
    this.setStatus("stopped");
  }

  /** Watch one instrument's prices. Returns the function that stops watching. */
  subscribeTicks(instrumentId: string, listener: (t: Tick) => void): () => void {
    let set = this.tickListeners.get(instrumentId);
    if (!set) this.tickListeners.set(instrumentId, (set = new Set()));
    set.add(listener);
    // A second widget on the same instrument gets the price at once instead of waiting
    // for the next tick (the server only sends the current price to the first subscribe).
    const cached = this.latest.get(instrumentId);
    if (cached) listener(cached);
    const count = (this.refs.get(instrumentId) ?? 0) + 1;
    this.refs.set(instrumentId, count);
    if (count === 1) this.send({ op: "subscribe", instruments: [instrumentId] });
    let done = false;
    return () => {
      if (done) return;
      done = true;
      set!.delete(listener);
      const left = (this.refs.get(instrumentId) ?? 1) - 1;
      if (left <= 0) {
        this.refs.delete(instrumentId);
        this.tickListeners.delete(instrumentId);
        this.latest.delete(instrumentId);
        this.send({ op: "unsubscribe", instruments: [instrumentId] });
      } else {
        this.refs.set(instrumentId, left);
      }
    };
  }

  /**
   * Watch an option chain (the server sends it at once, then at most once a second). Widgets
   * watching the same underlying + expiry share one server chain; the strikes asked last win.
   */
  subscribeChain(req: ChainRequest, listener: (c: OptionChain) => void): () => void {
    const key = `${req.underlying}|${req.expiry}`;
    let entry = this.chains.get(key);
    if (!entry) this.chains.set(key, (entry = { req, listeners: new Set() }));
    const changed = entry.req.strikes !== req.strikes || entry.listeners.size === 0;
    entry.req = req;
    entry.listeners.add(listener);
    if (changed) this.send({ op: "chain", ...req });
    return () => {
      const current = this.chains.get(key);
      if (!current) return;
      current.listeners.delete(listener);
      if (current.listeners.size === 0) {
        this.chains.delete(key);
        this.send({ op: "chain_stop", underlying: req.underlying, expiry: req.expiry });
      }
    };
  }

  /** Instruments with at least one watcher (for tests and the status bar). */
  watched(): string[] {
    return [...this.refs.keys()];
  }

  on<K extends ServerMessage["type"]>(type: K, listener: (m: MessageOf<K>) => void): () => void {
    let set = this.listeners.get(type);
    if (!set) this.listeners.set(type, (set = new Set()));
    const wrapped = listener as (m: ServerMessage) => void;
    set.add(wrapped);
    return () => set!.delete(wrapped);
  }

  onStatus(listener: (s: Status) => void): () => void {
    this.statusListeners.add(listener);
    return () => this.statusListeners.delete(listener);
  }

  private connect(status: Status): void {
    this.setStatus(status);
    const create = this.opts.createSocket ?? ((url: string) => new WebSocket(url) as SocketLike);
    const socket = create(this.opts.url);
    this.socket = socket;
    socket.onopen = () => {
      this.attempt = 0;
      const ids = [...this.refs.keys()];
      if (ids.length) socket.send(JSON.stringify({ op: "subscribe", instruments: ids }));
      for (const { req } of this.chains.values()) socket.send(JSON.stringify({ op: "chain", ...req }));
      this.setStatus("open");
    };
    socket.onmessage = (ev) => this.dispatch(String(ev.data));
    socket.onerror = () => {};
    socket.onclose = (ev) => {
      if (this.socket !== socket) return;
      this.socket = null;
      if (ev.code === CLOSE_NOT_LOGGED_IN) {
        this.wanted = false;
        this.setStatus("logged-out");
        return;
      }
      if (!this.wanted) return;
      const delay = BACKOFF_MS[Math.min(this.attempt, BACKOFF_MS.length - 1)]!;
      this.attempt += 1;
      this.setStatus("reconnecting");
      this.timer = (this.opts.setTimer ?? setTimeout)(() => {
        this.timer = null;
        if (this.wanted) this.connect("reconnecting");
      }, delay);
    };
  }

  private dispatch(text: string): void {
    let message: ServerMessage;
    try {
      message = JSON.parse(text) as ServerMessage;
    } catch {
      return;
    }
    if (message.type === "ticks") {
      this.lastTickAt = Date.now();
      for (const tick of message.ticks) {
        if (!this.refs.has(tick.instrument_id)) continue; // a late tick after unsubscribing
        this.latest.set(tick.instrument_id, tick);
        for (const listener of this.tickListeners.get(tick.instrument_id) ?? []) listener(tick);
      }
    }
    if (message.type === "chain") {
      for (const listener of this.chains.get(`${message.underlying}|${message.expiry}`)?.listeners ?? []) {
        try {
          listener(message);
        } catch (err) {
          console.error("chain listener failed", err);
        }
      }
    }
    for (const listener of this.listeners.get(message.type) ?? []) {
      try {
        listener(message);
      } catch (err) {
        console.error("stream listener failed", err);
      }
    }
  }

  private send(message: object): void {
    if (this.socket && this.socket.readyState === OPEN) this.socket.send(JSON.stringify(message));
    // not open: the subscription list is re-sent on (re)connect
  }

  private setStatus(status: Status): void {
    if (this.status === status) return;
    this.status = status;
    for (const listener of this.statusListeners) listener(status);
  }
}
