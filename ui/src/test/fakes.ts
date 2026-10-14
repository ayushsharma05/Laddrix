// Test doubles: a scripted server for Rest, a WebSocket we drive by hand, ticks.

import { Rest } from "../api/rest";
import { StreamClient, type SocketLike } from "../api/stream";
import type { Tick } from "../api/types";

type Handler = (body: unknown, method: string) => { status: number; body?: unknown };

/** A Rest whose server is a table of handlers keyed by "METHOD /path". */
export function fakeRest(routes: Record<string, Handler | unknown> = {}) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  const rest = new Rest(async (path, init) => {
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path, body });
    const route = routes[`${method} ${path}`] ?? routes[`${method} ${path.split("?")[0]}`];
    if (route === undefined) return new Response(JSON.stringify({ detail: "not found" }), { status: 404 });
    const result =
      typeof route === "function" ? (route as Handler)(body, method) : { status: 200, body: route };
    if (result.status === 0) throw new TypeError("network down");
    return new Response(result.body === undefined ? null : JSON.stringify(result.body), {
      status: result.status,
    });
  });
  return { rest, calls, routes };
}

export class FakeSocket implements SocketLike {
  static all: FakeSocket[] = [];
  readyState = 0;
  sent: unknown[] = [];
  closedWith: number | null = null;
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;

  constructor(readonly url: string) {
    FakeSocket.all.push(this);
  }
  send(data: string): void {
    this.sent.push(JSON.parse(data));
  }
  close(code = 1000): void {
    this.closedWith = code;
    this.readyState = 3;
  }
  // --- driven by the test ---
  open(): void {
    this.readyState = 1;
    this.onopen?.(new Event("open"));
  }
  receive(message: unknown): void {
    this.onmessage?.({ data: JSON.stringify(message) } as MessageEvent);
  }
  drop(code = 1006): void {
    this.readyState = 3;
    this.onclose?.({ code } as CloseEvent);
  }
}

/** A StreamClient on fake sockets with timers the test fires by hand. */
export function fakeStream() {
  FakeSocket.all = [];
  const timers: { fn: () => void; ms: number }[] = [];
  const stream = new StreamClient({
    url: "ws://test/ws",
    createSocket: (url) => new FakeSocket(url),
    setTimer: (fn, ms) => {
      timers.push({ fn, ms });
      return timers.length as unknown as ReturnType<typeof setTimeout>;
    },
    clearTimer: () => {},
  });
  const socket = () => FakeSocket.all[FakeSocket.all.length - 1]!;
  return { stream, timers, socket };
}

export function tick(instrumentId: string, ltp: number, extra: Partial<Tick> = {}): Tick {
  return {
    instrument_id: instrumentId,
    ltp_paise: ltp,
    last_qty: 75,
    volume: 1234500,
    avg_price_paise: null,
    buy_qty: null,
    sell_qty: null,
    open_paise: null,
    high_paise: null,
    low_paise: null,
    close_paise: null,
    open_interest: null,
    exchange_time: null,
    bids: [],
    asks: [],
    ...extra,
  };
}
