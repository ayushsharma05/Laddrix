// The services every widget shares, and the hooks widgets use to read them.
//
//   <AppProvider app={…}>          one per browser tab
//     useApp()                     rest, stream, store, session, instruments, links
//     useStore(s => s.something)   re-renders when that piece of state changes
//     useTick(id)                  the newest price for one instrument (shared subscription)
//     useInstrument(id)            tick size, lot size, symbol
//     useKeyAction(scope)          which shortcut a key press is (K13.4)

import { createContext, useContext, useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { Rest } from "../api/rest";
import { StreamClient } from "../api/stream";
import type { Instrument, OrderSpec, Tick } from "../api/types";
import { Instruments } from "../state/instruments";
import { OrderSpecs } from "../state/orderSpecs";
import { Session } from "../state/session";
import { Store, type State } from "../state/store";
import { LinkBus } from "../workspace/links";
import { KeyBindings } from "../keys/bindings";
import { actionFor, chordOf, type Bindings, type Scope } from "../keys/model";

export interface App {
  rest: Rest;
  stream: StreamClient;
  store: Store;
  session: Session;
  instruments: Instruments;
  specs: OrderSpecs;
  links: LinkBus;
  keys: KeyBindings; // the user's keyboard shortcuts (K13.4)
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
    specs: new OrderSpecs(rest),
    links: new LinkBus(),
    keys: new KeyBindings(rest),
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
  // never the previous instrument's tick, even for the one render before the effect runs
  return tick && tick.instrument_id === instrumentId ? tick : null;
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
  // never the previous instrument (a ticket that switched would start at its price)
  if (!instrumentId) return null;
  if (found && found.id === instrumentId) return found;
  return instruments.peek(instrumentId) ?? null;
}

/** What can be ordered on an instrument through an account, or null while loading / unavailable. */
export function useOrderSpec(accountId: string | null | undefined, instrumentId: string | undefined): OrderSpec | null {
  const { specs } = useApp();
  const key = accountId && instrumentId ? `${accountId}|${instrumentId}` : null;
  // kept with the account and instrument it is for: never another pair's rules
  const [found, setFound] = useState<{ key: string; spec: OrderSpec | null } | null>(() =>
    key ? { key, spec: specs.peek(accountId!, instrumentId!) ?? null } : null,
  );
  useEffect(() => {
    let live = true;
    if (!key) {
      setFound(null);
      return;
    }
    const known = specs.peek(accountId!, instrumentId!);
    setFound({ key, spec: known ?? null });
    if (known) return;
    void specs.get(accountId!, instrumentId!).then((s) => {
      if (live) setFound({ key, spec: s });
    });
    return () => {
      live = false;
    };
  }, [specs, key, accountId, instrumentId]);
  if (!key) return null;
  if (found && found.key === key) return found.spec;
  return specs.peek(accountId!, instrumentId!) ?? null;
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

/** The user's shortcut choices (re-renders when they change). */
export function useKeyBindings(): Bindings {
  const { keys } = useApp();
  return useSyncExternalStore(keys.subscribe, keys.get);
}

/** For a widget's onKeyDown: which action this key press starts in its scope (or null). */
export function useKeyAction(scope: Scope): (e: { key: string; code: string; ctrlKey: boolean; metaKey: boolean; altKey: boolean; shiftKey: boolean }) => string | null {
  const bindings = useKeyBindings();
  return (e) => actionFor(chordOf(e), scope, bindings);
}
