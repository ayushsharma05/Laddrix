// The ladder: the trader's main order entry (docs/design/ui.md §4, canvas board 0).
//
//   ┌ header: net change · H L O V · position and P&L ───────────────────────────┐
//   │ sidebar │ Work │ PIQ │ Bids │ Price │ Asks │ LTQ │ VAP                     │
//   │ Acct    │      │     │  150 │25,010.00│    │  75 │ ███ 600                 │
//   │ Type…   │  ... one row per price on the tick grid, highest first ...       │
//   └ status line: the last thing that happened here · working quantity ───────────┘
//
// Click Bids = buy the lots box at that price; click Asks = sell. Click Work = select my
// orders there; again = cancel; another row's Work = move them there. Every click passes
// tradingBlock() first, and the server checks everything again (risk, throttle, kill switch).

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../../api/rest";
import type { AlgoView, Instrument, OrderView, Side, Tick } from "../../api/types";
import { useApp, useInstrument, useKeyAction, useOrderSpec, useStore } from "../../app/context";
import { ExpiryNote } from "../../components/ExpiryBadge";
import { SearchBox } from "../../components/SearchBox";
import { change, price, qty as fmtQty, rupees, shortName, shortVolume } from "../../lib/format";
import { cancelBlock, tradingBlock } from "../../state/gate";
import type { Message } from "../../state/store";
import type { MenuItem } from "../../components/ContextMenu";
import { registerWidget, type BaseParams, type WidgetProps } from "../../workspace/registry";
import {
  buildOrder,
  buildRows,
  isSpread,
  needsRecentre,
  orderValue,
  priceOf,
  clickTypes,
  QueueTracker,
  rowPrices,
  snap,
  SPREAD_PREFIX,
  spreadWorking,
  unconfirmed,
  VapCounter,
  workingOrders,
  type Tif,
} from "./model";

export interface LadderParams extends BaseParams {
  lots: number;
  type: string; // an order type code from the order spec ("LIMIT", "MARKET"…)
  tif: Tif;
  product?: string; // unset: the first that fits the instrument
  accountId?: string; // unset: the account picked in the toolbar
  confirmOverPaise: number; // 0 = never ask; else orders worth more need a second click
  label?: string; // a spread ladder's title: the spread's name (its id says nothing)
}

export const LOT_PRESETS = [1, 2, 5, 10, 25, 50];
export const CONFIRM_CHOICES: [number, string][] = [
  [0, "Off"],
  [1_00_000_00, "₹1L"],
  [5_00_000_00, "₹5L"],
  [25_00_000_00, "₹25L"],
];
const ROW_PX = 20;
const DEFAULT_ROWS = 30;
const ARM_MS = 5000; // a "click again to confirm" expires after this

type Note = { at: number; level: Message["level"]; text: string };
type Armed = { side: Side; price: number; until: number };

export function Ladder(props: WidgetProps<LadderParams>) {
  const id = props.params.instrumentId;
  const instrument = useInstrument(id);
  if (!id) return <PickInstrument onPick={(i) => props.setInstrument(i)} />;
  if (!instrument) return <div className="empty-note">Loading {shortName(id)}…</div>;
  return <LadderBody key={id} {...props} instrument={instrument} />;
}

function PickInstrument({ onPick }: { onPick: (id: string) => void }) {
  return (
    <div className="ladder-pick">
      <div className="empty-note">Pick a contract for this ladder.</div>
      <SearchBox placeholder="Search product…" ariaLabel="Ladder instrument" onPick={(i) => onPick(i.id)} />
    </div>
  );
}

function LadderBody({ params, setParams, instrument }: WidgetProps<LadderParams> & { instrument: Instrument }) {
  const keyAction = useKeyAction("ladder");
  const app = useApp();
  const { rest, stream, store } = app;
  const id = instrument.id;
  const tickSize = instrument.tick_paise;
  // a spread ladder (#416): its prices are the spread's implied ones; a click starts the ASE
  // algo; the Work column shows those algos. The server works out every leg.
  const spread = isSpread(id);
  // the window shows the spread's name, kept in step when it is renamed
  useEffect(() => {
    if (spread && params.label !== instrument.symbol) setParams({ label: instrument.symbol });
  }, [spread, params.label, instrument.symbol, setParams]);

  // --- which account ------------------------------------------------------------------------
  const me = useStore((s) => s.me);
  const globalAccount = useStore((s) => s.accountId);
  const accountId = params.accountId && me?.accounts.some((a) => a.id === params.accountId) ? params.accountId : globalAccount;
  const orders = useStore((s) => (accountId ? s.books[accountId]?.orders : undefined));
  const algos = useStore((s) => (accountId ? s.books[accountId]?.algos : undefined));
  const positions = useStore((s) => (accountId ? s.books[accountId]?.positions : undefined));
  const blocked = useStore((s) => tradingBlock(s, accountId));
  const messages = useStore((s) => s.messages);

  // --- prices, queue estimates, volume at price -----------------------------------------------
  // one queue tracker and one volume counter for the ladder's life (they count tick by tick)
  const [queue] = useState(() => new QueueTracker());
  const [vap] = useState(() => new VapCounter());
  const [tick, setTick] = useState<Tick | null>(null);
  const [vapVersion, setVapVersion] = useState(0);
  const lastLtp = useRef<number | null>(null);
  const [ltqUp, setLtqUp] = useState(true);

  useEffect(() => {
    return stream.subscribeTicks(id, (t) => {
      const book = accountId ? store.getState().books[accountId]?.orders : undefined;
      queue.update(t, workingOrders(book ?? {}, id));
      vap.onTick(t);
      if (lastLtp.current !== null && t.ltp_paise !== lastLtp.current) setLtqUp(t.ltp_paise > lastLtp.current);
      lastLtp.current = t.ltp_paise;
      setTick(t);
    });
  }, [stream, store, id, accountId, queue, vap]);

  // the day's volume at price from the server, again after every reconnect (a spread has none)
  useEffect(() => {
    if (spread) return;
    let live = true;
    const load = () => {
      rest
        .get<{ volume: number | null; levels: [number, number][] }>(`/api/instruments/${encodeURIComponent(id)}/vap`)
        .then((snap) => {
          if (!live) return;
          vap.reset(snap.volume, snap.levels);
          setVapVersion((v) => v + 1);
        })
        .catch(() => {}); // VAP is a nicety: the column fills from the stream instead
    };
    load();
    const stop = stream.onStatus((s) => {
      if (s === "open") load();
    });
    return () => {
      live = false;
      stop();
    };
  }, [rest, stream, id, vap, spread]);

  // --- rows and centring ------------------------------------------------------------------------
  const gridRef = useRef<HTMLDivElement>(null);
  const [rowCount, setRowCount] = useState(DEFAULT_ROWS);
  useLayoutEffect(() => {
    const el = gridRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      const fits = Math.floor(el.clientHeight / ROW_PX);
      if (fits > 0) setRowCount(fits);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const [centre, setCentre] = useState<number | null>(null);
  const [auto, setAuto] = useState(true);
  const ltp = tick?.ltp_paise ?? instrument.ltp_paise ?? null;
  // centred on the price once it is known, and again when it walks off (while drawing: the
  // new centre is never one frame late)
  if (ltp !== null && (centre === null || (auto && needsRecentre(centre, ltp, rowCount, tickSize)))) setCentre(snap(ltp, tickSize));

  const working = useMemo(() => (spread ? spreadWorking(algos ?? {}, id) : workingOrders(orders ?? {}, id)), [spread, algos, orders, id]);
  const rows = useMemo(
    () =>
      centre === null ? [] : buildRows({ prices: rowPrices(centre, rowCount, tickSize), tick, working, queue, vap }),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- vapVersion: the counter changed in place (the server's count arrived)
    [centre, rowCount, tickSize, tick, working, queue, vap, vapVersion],
  );

  // --- the status line ---------------------------------------------------------------------------
  const [note, setNote] = useState<Note | null>(null);
  const info = useCallback((text: string) => setNote({ at: Date.now(), level: "INFO", text }), []);
  const warn = useCallback(
    (text: string) => {
      const m = store.addMessage("WARN", id, `${shortName(id)}: ${text}`);
      setNote({ at: m.at, level: "WARN", text });
    },
    [store, id],
  );
  const latestMessage = useMemo(() => messages.find((m) => m.instrumentId === id), [messages, id]);
  const status: Note | null =
    latestMessage && (!note || latestMessage.at > note.at)
      ? { at: latestMessage.at, level: latestMessage.level, text: latestMessage.text }
      : note;

  // --- the trading actions -----------------------------------------------------------------------
  // what a click can place, from the server's order spec (nothing hard-coded here)
  const spec = useOrderSpec(accountId, spread ? undefined : id); // a spread's legs are the server's
  const types = useMemo(() => clickTypes(spec), [spec]); // the same objects while the spec stands
  const orderType = types.find((t) => t.code === params.type || t.label === params.type) ?? types[0] ?? null;
  const tifs = orderType?.validities ?? [];
  const tif = params.tif && tifs.includes(params.tif) ? params.tif : (tifs[0] ?? "DAY");
  const products = spec?.products ?? [];
  const product = params.product && products.includes(params.product) ? params.product : (products[0] ?? "");
  const lots = Math.max(0, params.lots ?? 1);
  const [selected, setSelected] = useState<number | null>(null);
  const [armed, setArmed] = useState<Armed | null>(null);

  // a selection whose orders are gone (filled, cancelled) is dropped (while drawing)
  if (selected !== null && !working.some((o) => priceOf(o) === selected)) setSelected(null);

  /** A spread: buy or sell `lots` spread lots at `at` (the ASE algo quotes and hedges). */
  const sendSpread = useCallback(
    (side: Side, at: number, lotsNow: number) => {
      if (!accountId) return;
      const what = `${side === "BUY" ? "Buy" : "Sell"} ${lotsNow} ${instrument.symbol} @ ${price(at)}`;
      info(`${what} sent`);
      rest
        .post<AlgoView>(`/api/accounts/${encodeURIComponent(accountId)}/spreads/${encodeURIComponent(id.slice(SPREAD_PREFIX.length))}/orders`, {
          side,
          lots: lotsNow,
          price_paise: at,
        })
        .then((view) => store.applyAlgos(accountId, [view]))
        .catch((e: unknown) => warn(`${what} refused: ${e instanceof Error ? e.message : String(e)}`));
    },
    [accountId, instrument.symbol, id, rest, store, info, warn],
  );

  const send = useCallback(
    (side: Side, at: number, ctrl: boolean) => {
      const state = store.getState();
      const why = tradingBlock(state, accountId);
      if (why) return warn(`not sent: ${why}`);
      if (!accountId) return;
      if (!instrument.tradable) return warn(`not sent: ${instrument.symbol} can't be traded`);
      if (spread) {
        if (lots <= 0) return warn("not sent: quantity is 0, click a lot button first");
        const best = side === "BUY" ? tick?.bids.find((l) => l[1] > 0)?.[0] : tick?.asks.find((l) => l[1] > 0)?.[0];
        if (ctrl && best === undefined) return warn("not sent: no best price to join");
        return sendSpread(side, ctrl ? best! : at, lots);
      }
      if (!orderType || !product) return warn("not sent: what can be ordered here hasn't loaded yet");
      if (lots <= 0) return warn("not sent: quantity is 0, click a lot button first");
      if (unconfirmed(state.books[accountId]?.orders ?? {}, id).length) {
        return warn("not sent: an earlier order's outcome is unknown; waiting for the broker's order book");
      }
      let px = at;
      if (ctrl) {
        // Ctrl+click joins the best price on that side
        const best = side === "BUY" ? tick?.bids.find((l) => l[1] > 0)?.[0] : tick?.asks.find((l) => l[1] > 0)?.[0];
        if (best === undefined) return warn("not sent: no best price to join");
        px = best;
      }
      const body = buildOrder({ instrument, side, lots, price: px, type: orderType, tif, product });
      const value = orderValue(body, ltp);
      const what = `${side} ${fmtQty(body.qty)} ${instrument.symbol} @ ${body.price_paise === null ? orderType.label : price(body.price_paise)}`;
      const threshold = params.confirmOverPaise ?? 0;
      if (threshold > 0 && value > threshold) {
        const now = Date.now();
        if (!armed || armed.side !== side || armed.price !== px || armed.until < now) {
          setArmed({ side, price: px, until: now + ARM_MS });
          return info(`Click again to confirm ${what} (${rupees(value)})`);
        }
      }
      setArmed(null);
      if (body.price_paise !== null) queue.joined(body.client_order_id, side, body.price_paise, tick);
      store.noteSent();
      info(`${what} ${body.validity} sent`);
      rest
        .post<OrderView>(`/api/accounts/${encodeURIComponent(accountId)}/orders`, body)
        .then((view) => store.applyOrders(accountId, [view]))
        .catch((e: unknown) => {
          if (e instanceof ApiError && e.status === 0) {
            warn(`${what}: no answer from Laddrix, check the order book before sending again`);
          } else {
            warn(`${what} refused: ${e instanceof Error ? e.message : String(e)}`);
          }
        });
    },
    [store, accountId, warn, info, instrument, lots, id, tick, orderType, tif, params.confirmOverPaise, product, ltp, armed, queue, rest, spread, sendSpread],
  );

  const cancelOrders = useCallback(
    (list: OrderView[], label: string) => {
      const why = cancelBlock(store.getState(), accountId);
      if (why) return warn(`not cancelled: ${why}`);
      if (!accountId || !list.length) return;
      info(`Cancelling ${label}`);
      if (spread) {
        // each is an ASE algo: stopping it pulls its quote (its hedges still finish)
        for (const o of list) {
          rest
            .post<AlgoView>(`/api/accounts/${encodeURIComponent(accountId)}/algos/${o.request.client_order_id}/stop`, {})
            .then((view) => store.applyAlgos(accountId, [view]))
            .catch((e: unknown) => warn(`cancel ${o.request.client_order_id}: ${e instanceof Error ? e.message : String(e)}`));
        }
        return;
      }
      for (const o of list) {
        rest
          .delete<OrderView>(`/api/accounts/${encodeURIComponent(accountId)}/orders/${o.request.client_order_id}`)
          .then((view) => store.applyOrders(accountId, [view]))
          .catch((e: unknown) => warn(`cancel ${o.request.client_order_id}: ${e instanceof Error ? e.message : String(e)}`));
      }
    },
    [store, accountId, rest, info, warn, spread],
  );

  const moveOrders = useCallback(
    (from: number, to: number) => {
      const why = tradingBlock(store.getState(), accountId);
      if (why) return warn(`not moved: ${why}`);
      if (!accountId) return;
      const list = working.filter((o) => priceOf(o) === from && o.pending === "NONE");
      if (!list.length) return warn("nothing to move: those orders are being cancelled or checked");
      info(`Moving ${list.length} order(s) ${price(from)} → ${price(to)} (back of the queue)`);
      if (spread) {
        // an algo's level doesn't change in place: stop it, then start the rest at the new price
        for (const o of list) {
          const rest_lots = o.request.qty - o.filled_qty;
          rest
            .post<AlgoView>(`/api/accounts/${encodeURIComponent(accountId)}/algos/${o.request.client_order_id}/stop`, {})
            .then((view) => {
              store.applyAlgos(accountId, [view]);
              sendSpread(o.request.side, to, rest_lots);
            })
            .catch((e: unknown) => warn(`move refused: ${e instanceof Error ? e.message : String(e)}`));
        }
        return;
      }
      for (const o of list) {
        rest
          .patch<OrderView>(`/api/accounts/${encodeURIComponent(accountId)}/orders/${o.request.client_order_id}`, {
            price_paise: to,
          })
          .then((view) => store.applyOrders(accountId, [view]))
          .catch((e: unknown) => warn(`move refused: ${e instanceof Error ? e.message : String(e)}`));
      }
    },
    [store, accountId, rest, working, info, warn, spread, sendSpread],
  );

  const workClick = (at: number) => {
    const mine = working.filter((o) => priceOf(o) === at);
    if (mine.length) {
      if (selected === at) {
        setSelected(null);
        cancelOrders(mine, `${mine.length} order(s) at ${price(at)}`);
      } else {
        setSelected(at);
        info(`Selected ${price(at)}: click again to cancel, or another row's Work to move`);
      }
      return;
    }
    if (selected !== null) {
      const from = selected;
      setSelected(null);
      moveOrders(from, at);
    }
  };

  const recentre = () => {
    if (ltp !== null) setCentre(snap(ltp, tickSize));
  };
  const scroll = (rowsBy: number) => {
    setAuto(false);
    setCentre((c) => (c === null ? c : c + rowsBy * tickSize));
  };

  // --- header numbers ------------------------------------------------------------------------------
  const close = tick?.close_paise ?? null;
  const net = tick && close !== null ? tick.ltp_paise - close : null;
  const pos = useMemo(() => {
    let q = 0;
    let pnl = 0;
    for (const p of Object.values(positions ?? {})) {
      if (p.instrument_id !== id) continue;
      q += p.qty;
      pnl += p.pnl_paise;
    }
    return { qty: q, pnl };
  }, [positions, id]);
  const workingQty = working.reduce((sum, o) => sum + Math.max(0, o.request.qty - o.filled_qty), 0);
  const accounts = me?.accounts ?? [];

  return (
    <div
      className={`ladder${blocked ? " blocked" : ""}`}
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement) return; // its own fields
        const action = keyAction(e); // the user's keys for the ladder (K13.4)
        if (action === "ladder-centre") recentre();
        else if (action === "ladder-clear") {
          setSelected(null);
          setArmed(null);
          setParams({ lots: 0 });
        } else return;
        e.preventDefault();
      }}
    >
      <ExpiryNote info={spec?.expiry} />
      <div className="ladder-head">
        <span className={`ladder-net ${net === null ? "" : net >= 0 ? "good" : "bad"}`}>
          {net === null ? "–" : change(tick!.ltp_paise, close)}
        </span>
        <div className="ladder-hl">
          <span>
            H <b className="good">{tick?.high_paise ? price(tick.high_paise) : "–"}</b>
          </span>
          <span>O {tick?.open_paise ? price(tick.open_paise) : "–"}</span>
          <span>
            L <b className="bad">{tick?.low_paise ? price(tick.low_paise) : "–"}</b>
          </span>
          <span>V {tick?.volume != null ? shortVolume(tick.volume) : "–"}</span>
        </div>
        <span className="ladder-spacer" />
        <div className="ladder-pos" aria-label="Position and P&L">
          <span className="dim">Pos</span>
          <b className={pos.qty > 0 ? "buy-text" : pos.qty < 0 ? "sell-text" : ""} title={spread ? "A spread's position is in its legs (Positions)" : undefined}>
            {spread ? "–" : pos.qty > 0 ? `+${fmtQty(pos.qty)}` : fmtQty(pos.qty)}
          </b>
          <span className="dim">P&amp;L</span>
          <span className={pos.pnl > 0 ? "good" : pos.pnl < 0 ? "bad" : ""}>{rupees(pos.pnl, { sign: true })}</span>
        </div>
      </div>

      <div className="ladder-main">
        <div className="ladder-side">
          <label>
            Acct
            <select
              value={accountId ?? ""}
              onChange={(e) => setParams({ accountId: e.target.value })}
              title="The account this ladder trades"
            >
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.id}
                  {a.can_trade ? "" : " (view)"}
                </option>
              ))}
            </select>
          </label>
          {spread ? (
            <span className="ladder-spread-note" title="Buying the spread buys its + legs and sells its − legs; the Autospreader quotes one leg and hedges the others">
              Spread: the server trades each leg
            </span>
          ) : (
            <>
            <label>
              Type
              <select value={orderType?.code ?? ""} title={orderType?.help} onChange={(e) => setParams({ type: e.target.value })}>
                {types.map((t) => (
                  <option key={t.code} value={t.code} title={t.help}>
                    {t.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              TIF
              <select value={tif} onChange={(e) => setParams({ tif: e.target.value })}>
                {tifs.map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </select>
            </label>
            <label>
              Prod
              <select value={product} onChange={(e) => setParams({ product: e.target.value })}>
                {products.map((p) => (
                  <option key={p}>{p}</option>
                ))}
              </select>
            </label>
            <label title="Orders worth more than this need a second click">
              Cfm
              <select
                value={params.confirmOverPaise ?? 0}
                onChange={(e) => setParams({ confirmOverPaise: Number(e.target.value) })}
              >
                {CONFIRM_CHOICES.map(([v, label]) => (
                  <option key={v} value={v}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            </>
          )}
          <span className="ladder-side-label">Lots</span>
          <output
            className="ladder-lots"
            aria-label="Lots"
            title="Mouse wheel: ±1 lot"
            onWheel={(e) => setParams({ lots: Math.max(0, lots + (e.deltaY < 0 ? 1 : -1)) })}
          >
            {lots}
          </output>
          <span className="ladder-qty">{spread ? `spread lot${lots === 1 ? "" : "s"}` : `= ${fmtQty(lots * instrument.lot_size)} qty`}</span>
          <div className="ladder-presets">
            {LOT_PRESETS.map((n) => (
              <button key={n} type="button" onClick={() => setParams({ lots: lots + n })}>
                +{n}
              </button>
            ))}
          </div>
          <button type="button" onClick={() => setParams({ lots: 0 })} title="Clear lots (Esc)">
            0
          </button>
          <span className="ladder-spacer" />
          <button type="button" aria-label="Scroll prices up" onClick={() => scroll(5)}>
            ▲
          </button>
          <button
            type="button"
            className={auto ? "on" : ""}
            title={auto ? "Auto-centre is on: click to re-centre and turn it off" : "Re-centre and turn auto-centre on (Space re-centres)"}
            onClick={() => {
              recentre();
              setAuto(!auto);
            }}
          >
            {auto ? "AUTO" : "CTR"}
          </button>
          <button type="button" aria-label="Scroll prices down" onClick={() => scroll(-5)}>
            ▼
          </button>
          <button
            type="button"
            className="ladder-cxl"
            title={`Cancel all my working orders on ${instrument.symbol}`}
            onClick={() => cancelOrders(working, `all ${working.length} working order(s) on ${instrument.symbol}`)}
          >
            CXL
            <br />
            ALL
          </button>
        </div>

        <div className="ladder-grid" role="grid" aria-label={`${instrument.symbol} ladder`}>
          <div className="ladder-row ladder-hd" role="row">
            <span role="columnheader">Work</span>
            <span role="columnheader" title="Quantity ahead of my order (an estimate)">PIQ</span>
            <span role="columnheader">Bids</span>
            <span role="columnheader">Price</span>
            <span role="columnheader">Asks</span>
            <span role="columnheader" title="Last traded quantity">LTQ</span>
            <span role="columnheader" title="Today's volume at each price (an estimate from snapshots)">VAP</span>
          </div>
          <div
            className="ladder-rows"
            ref={gridRef}
            onWheel={(e) => scroll(e.deltaY < 0 ? 1 : -1)}
          >
            {centre === null && <div className="empty-note">Waiting for prices…</div>}
            {rows.map((r) => {
              const px = price(r.price);
              const work = r.workBuy || r.workSell;
              const isArmed = armed !== null && armed.price === r.price;
              return (
                <div key={r.price} className="ladder-row" role="row" data-price={r.price}>
                  <button
                    type="button"
                    className={`lc work${r.workBuy ? " work-buy" : r.workSell ? " work-sell" : ""}${selected === r.price ? " selected" : ""}${r.pendingCancel ? " pending" : ""}`}
                    aria-label={
                      work
                        ? `${selected === r.price ? "Cancel" : "Select"} my orders at ${px}`
                        : `Move selected orders to ${px}`
                    }
                    title={r.pendingCancel ? "Pending cancel" : undefined}
                    onClick={() => workClick(r.price)}
                  >
                    {r.workBuy ? fmtQty(r.workBuy) : r.workSell ? fmtQty(r.workSell) : ""}
                  </button>
                  <span className="lc piq">{r.piq !== null && work ? fmtQty(r.piq) : ""}</span>
                  <button
                    type="button"
                    className={`lc bid${r.bid !== null ? " has" : ""}${r.bestBid ? " best" : ""}${isArmed && armed.side === "BUY" ? " armed" : ""}`}
                    aria-label={`Buy at ${px}`}
                    disabled={!!blocked}
                    onClick={(e) => send("BUY", r.price, e.ctrlKey || e.metaKey)}
                  >
                    {r.bid !== null ? fmtQty(r.bid) : ""}
                  </button>
                  <span className={`lc price${r.last ? " last" : ""}`}>{px}</span>
                  <button
                    type="button"
                    className={`lc ask${r.ask !== null ? " has" : ""}${r.bestAsk ? " best" : ""}${isArmed && armed.side === "SELL" ? " armed" : ""}`}
                    aria-label={`Sell at ${px}`}
                    disabled={!!blocked}
                    onClick={(e) => send("SELL", r.price, e.ctrlKey || e.metaKey)}
                  >
                    {r.ask !== null ? fmtQty(r.ask) : ""}
                  </button>
                  <span className={`lc ltq${r.ltq !== null ? (ltqUp ? " up" : " down") : ""}`}>
                    {r.ltq !== null ? fmtQty(r.ltq) : ""}
                  </span>
                  <span className="lc vap" style={r.vap ? { backgroundSize: `${r.vapShare}% 100%` } : undefined}>
                    {r.vap ? shortVolume(r.vap) : ""}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <div className="ladder-status" role="status">
        <span className={`dot ${status?.level ?? "INFO"}`} />
        <span className="text" title={blocked ?? status?.text}>
          {blocked
            ? `Trading paused: ${blocked}`
            : (status?.text ??
              (spread
                ? "Bids = buy the spread · Asks = sell it · Work: select / again = cancel / other row = move"
                : "Bids = buy · Asks = sell · Work: select / again = cancel / other row = move"))}
        </span>
        <span className="dim" title="Kite sends 5 levels of depth, about once a second">
          5 lvl · Wkg {fmtQty(workingQty)}
        </span>
      </div>
    </div>
  );
}

registerWidget<LadderParams>({
  kind: "ladder",
  title: "Ladder",
  description: "Price ladder: click Bids to buy, Asks to sell, Work to cancel or move",
  component: Ladder,
  defaults: () => ({ link: "pink", lots: 1, type: "LIMIT", tif: "DAY", confirmOverPaise: 0 }),
  link: "follows",
  tabTitle: (p) => (p.instrumentId ? (isSpread(p.instrumentId) && p.label ? p.label : shortName(p.instrumentId)) : undefined),
  size: { width: 470 },
  menu: (p, act) => [
    // a spread ladder (#416): its definition, in the Autospreader, ready to edit
    ...(isSpread(p.instrumentId)
      ? [{ label: "Autospreader settings…", onSelect: () => act.open("ase", { mode: "edit", spreadId: p.instrumentId!.slice(SPREAD_PREFIX.length) }) }, "separator" as const]
      : []),
    { label: "Lots per click", items: LOT_PRESETS.map((n) => ({ label: String(n), checked: p.lots === n, onSelect: () => act.setParams({ lots: n }) })) },
    { label: "Confirm orders over", items: confirmItems(p.confirmOverPaise, (v) => act.setParams({ confirmOverPaise: v })) },
  ],
});

/** "Confirm orders over" as right-click items (the ladder and the ticket share them). */
export function confirmItems(now: number, pick: (paise: number) => void): MenuItem[] {
  return CONFIRM_CHOICES.map(([v, label]) => ({ label, checked: now === v, onSelect: () => pick(v) }));
}
