// The services every widget shares, and the hooks widgets use to read them.
//
//   <AppProvider app={…}>          one per browser tab
//     useApp()                     rest, stream, store, session, instruments, links
//     useStore(s => s.something)   re-renders when that piece of state changes
//     useTick(id)                  the newest price for one instrument (shared subscription)
//     useInstrument(id)            tick size, lot size, symbol

import { createContext, useContext, useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { Rest } from "../api/rest";
import { StreamClient } from "../api/stream";
import type { Instrument, Tick } from "../api/types";
import { Instruments } from "../state/instruments";
import { Session } from "../state/session";
import { Store, type State } from "../state/store";
import { LinkBus } from "../workspace/links";

export interface App {
  rest: Rest;
  stream: StreamClient;
  store: Store;
  session: Session;
  instruments: Instruments;
  links: LinkBus;
}

/** Wires the real services for the browser. Tests build their own with fakes. */
export function createApp(opts: { rest?: Rest; stream?: StreamClient; store?: Store } = {}): App {
  const rest = opts.rest ?? new Rest();
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  const stream = opts.stream ?? new StreamClient({ url: `${scheme}//${location.host}/ws` });
  const store = opts.store ?? new Store();
  return {
    rest,
    stream,
    store,
    session: new Session(rest, stream, store),
    instruments: new Instruments(rest),
    links: new LinkBus(),
  };
}

const AppContext = createContext<App | null>(null);

export function AppProvider({ app, children }: { app: App; children: ReactNode }) {
  return <AppContext.Provider value={app}>{children}</AppContext.Provider>;
}

export function useApp(): App {
  const app = useContext(AppContext);
  if (!app) throw new Error("useApp outside <AppProvider>");
  return app;
}

/**
 * One piece of the shared state. The selector must return something already in the state
 * (or a primitive), never a new object or array, or React would re-render forever.
 */
export function useStore<T>(select: (s: State) => T): T {
  const { store } = useApp();
  return useSyncExternalStore(store.subscribe, () => select(store.getState()));
}

/** The newest tick for an instrument, or null until the first one arrives. */
export function useTick(instrumentId: string | undefined): Tick | null {
  const { stream } = useApp();
  const [tick, setTick] = useState<Tick | null>(null);
  useEffect(() => {
    setTick(null);
    if (!instrumentId) return;
    return stream.subscribeTicks(instrumentId, setTick);
  }, [stream, instrumentId]);
  return tick;
}

/** Instrument details, or null while loading / if unknown. */
export function useInstrument(instrumentId: string | undefined): Instrument | null {
  const { instruments } = useApp();
  const [found, setFound] = useState<Instrument | null>(() =>
    instrumentId ? (instruments.peek(instrumentId) ?? null) : null,
  );
  useEffect(() => {
    let live = true;
    if (!instrumentId) {
      setFound(null);
      return;
    }
    const known = instruments.peek(instrumentId);
    if (known) {
      setFound(known);
      return;
    }
    setFound(null);
    void instruments.get(instrumentId).then((i) => {
      if (live) setFound(i);
    });
    return () => {
      live = false;
    };
  }, [instruments, instrumentId]);
  return found;
}

/** Re-render every `ms` (clocks, "x seconds ago"). */
export function useNow(ms = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(timer);
  }, [ms]);
  return now;
}
