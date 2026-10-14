// Order ticket: every order type from a form (docs/design/ui.md §4, canvas board 3).
//
//   NIFTY26OCTFUT · LTP 25,010.00 · bid 25,010.00 / ask 25,010.10
//   [ BUY ][ SELL ]   Lots [−] 2 [+] = 150      Type LMT MKT SL SL-M
//   Price  [−] 25,010.00 [+]      Trigger [−] 25,015.00 [+]     TIF  Prod  Acct
//   Value ₹37,51,500.00 · worst position +225 · +0.00% from last
//   [ BUY 150 NIFTY26OCTFUT @ 25,010.00 ]     → over "confirm over": [CONFIRM BUY 150] [Back]
//
// Enter sends, Esc clears, ↑/↓ move the price one tick. The same gate as the ladder
// (tradingBlock) runs before every send, and the server checks everything again.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../../api/rest";
import type { Instrument, OrderView, Side } from "../../api/types";
import { useApp, useInstrument, useStore, useTick } from "../../app/context";
import { SearchBox } from "../../components/SearchBox";
import { price as fmtPrice, qty as fmtQty, rupees, shortName } from "../../lib/format";
import { tradingBlock } from "../../state/gate";
import { registerWidget, type BaseParams, type WidgetProps } from "../../workspace/registry";
import { CONFIRM_CHOICES } from "../ladder/Ladder";
import { leaves, productsFor, snap, unconfirmed, workingOrders, type Tif } from "../ladder/model";
import {
  buildTicketOrder,
  needsPrice,
  needsTrigger,
  parsePrice,
  stepPrice,
  summarise,
  ticketProblem,
  TICKET_TYPES,
  type TicketType,
} from "./model";

export interface TicketParams extends BaseParams {
  lots: number;
  type: TicketType;
  tif: Tif;
  product?: string;
  accountId?: string;
  confirmOverPaise: number;
}

export function Ticket(props: WidgetProps<TicketParams>) {
  const id = props.params.instrumentId;
  const instrument = useInstrument(id);
  if (!id) {
    return (
      <div className="ladder-pick">
        <div className="empty-note">Pick a contract for this ticket.</div>
        <SearchBox placeholder="Search product…" ariaLabel="Ticket instrument" onPick={(i) => props.setInstrument(i.id)} />
      </div>
    );
  }
  if (!instrument) return <div className="empty-note">Loading {shortName(id)}…</div>;
  return <TicketBody key={id} {...props} instrument={instrument} />;
}

const toText = (paise: number | null) => (paise === null ? "" : fmtPrice(paise));

function TicketBody({ params, setParams, instrument }: WidgetProps<TicketParams> & { instrument: Instrument }) {
  const { rest, store } = useApp();
  const id = instrument.id;
  const tick = useTick(id);
  const me = useStore((s) => s.me);
  const globalAccount = useStore((s) => s.accountId);
  const accountId = params.accountId && me?.accounts.some((a) => a.id === params.accountId) ? params.accountId : globalAccount;
  const orders = useStore((s) => (accountId ? s.books[accountId]?.orders : undefined));
  const positions = useStore((s) => (accountId ? s.books[accountId]?.positions : undefined));
  const blocked = useStore((s) => tradingBlock(s, accountId));

  const type = params.type ?? "LMT";
  const tif = params.tif ?? "DAY";
  const lots = Math.max(0, params.lots ?? 1);
  const products = productsFor(instrument);
  const product = params.product && products.includes(params.product) ? params.product : products[0]!;
  const tickSize = instrument.tick_paise;
  const ltp = tick?.ltp_paise ?? instrument.ltp_paise ?? null;

  const [side, setSide] = useState<Side>("BUY");
  const [priceText, setPriceText] = useState("");
  const [triggerText, setTriggerText] = useState("");
  const [armed, setArmed] = useState(false);
  const [note, setNote] = useState<{ level: "INFO" | "WARN"; text: string } | null>(null);
  const backRef = useRef<HTMLButtonElement>(null);

  // start the price at the last price once it's known
  useEffect(() => {
    if (priceText === "" && ltp !== null) setPriceText(toText(snap(ltp, tickSize)));
  }, [ltp, priceText, tickSize]);

  const price = parsePrice(priceText);
  const trigger = parsePrice(triggerText);
  const input = { instrument, side, lots, type, price, trigger, tif, product };
  const problem = ticketProblem(input, ltp);

  const position = useMemo(
    () => Object.values(positions ?? {}).filter((p) => p.instrument_id === id).reduce((n, p) => n + p.qty, 0),
    [positions, id],
  );
  const workingSameSide = useMemo(
    () => workingOrders(orders ?? {}, id).filter((o) => o.request.side === side).reduce((n, o) => n + leaves(o), 0),
    [orders, id, side],
  );
  const summary = summarise(input, ltp, position, workingSameSide);
  const qty = lots * instrument.lot_size;
  const what = `${side} ${fmtQty(qty)} ${instrument.symbol}${needsPrice(type) && price !== null ? ` @ ${fmtPrice(price)}` : ` ${type}`}${needsTrigger(type) && trigger !== null ? ` trigger ${fmtPrice(trigger)}` : ""}`;

  // any change to the order disarms a pending confirmation
  useEffect(() => setArmed(false), [side, priceText, triggerText, lots, type, tif, product, accountId]);
  useEffect(() => {
    if (armed) backRef.current?.focus();
  }, [armed]);

  const warn = useCallback(
    (text: string) => {
      store.addMessage("WARN", id, `${instrument.symbol}: ${text}`);
      setNote({ level: "WARN", text });
    },
    [store, id, instrument.symbol],
  );

  const send = () => {
    const state = store.getState();
    const why = tradingBlock(state, accountId);
    if (why) return warn(`not sent: ${why}`);
    if (!accountId) return;
    if (problem) return warn(`not sent: ${problem}`);
    if (unconfirmed(state.books[accountId]?.orders ?? {}, id).length) {
      return warn("not sent: an earlier order's outcome is unknown; waiting for the broker's order book");
    }
    const threshold = params.confirmOverPaise ?? 0;
    if (!armed && threshold > 0 && (summary.value ?? 0) > threshold) {
      setArmed(true);
      setNote({ level: "INFO", text: `${what} is worth ${rupees(summary.value ?? 0)}: confirm or go back` });
      return;
    }
    setArmed(false);
    const body = buildTicketOrder(input);
    store.noteSent();
    setNote({ level: "INFO", text: `${what} ${tif} sent` });
    rest
      .post<OrderView>(`/api/accounts/${encodeURIComponent(accountId)}/orders`, body)
      .then((view) => {
        store.applyOrders(accountId, [view]);
        if (view.status === "REJECTED") setNote({ level: "WARN", text: `${what} refused: ${view.reason}` });
      })
      .catch((e: unknown) =>
        warn(e instanceof ApiError && e.status === 0 ? `${what}: no answer from MockTrade, check the order book before sending again` : `${what} refused: ${e instanceof Error ? e.message : String(e)}`),
      );
  };

  const clear = () => {
    setArmed(false);
    setTriggerText("");
    setPriceText(ltp !== null ? toText(snap(ltp, tickSize)) : "");
    setNote(null);
  };
  const nudge = (which: "price" | "trigger", ticks: number) => {
    const current = which === "price" ? price : trigger;
    const start = current ?? (ltp !== null ? snap(ltp, tickSize) : null);
    if (start === null) return;
    const next = toText(stepPrice(start, tickSize, current === null ? 0 : ticks));
    if (which === "price") setPriceText(next);
    else setTriggerText(next);
  };

  return (
    <div
      className={`ticket ${side === "BUY" ? "buy" : "sell"}${blocked ? " blocked" : ""}`}
      onKeyDown={(e) => {
        if (e.key === "Enter" && !(e.target instanceof HTMLButtonElement)) {
          e.preventDefault();
          send();
        } else if (e.key === "Escape") {
          e.preventDefault();
          clear();
        } else if ((e.key === "ArrowUp" || e.key === "ArrowDown") && !(e.target instanceof HTMLSelectElement)) {
          e.preventDefault();
          nudge(e.target instanceof HTMLInputElement && e.target.name === "trigger" ? "trigger" : "price", e.key === "ArrowUp" ? 1 : -1);
        }
      }}
    >
      <div className="ticket-head">
        <b>{instrument.symbol}</b>
        <span className="dim">{instrument.exchange} · lot {instrument.lot_size} · tick {fmtPrice(tickSize)}</span>
        <span className="book-spacer" />
        <span>
          LTP <b>{ltp !== null ? fmtPrice(ltp) : "–"}</b>
        </span>
        <span className="buy-text">{tick?.bids[0] ? fmtPrice(tick.bids[0][0]) : ""}</span>
        <span className="sell-text">{tick?.asks[0] ? fmtPrice(tick.asks[0][0]) : ""}</span>
      </div>

      <div className="ticket-grid">
        <div className="ticket-sides" role="group" aria-label="Side">
          <button type="button" className={`side buy${side === "BUY" ? " on" : ""}`} aria-pressed={side === "BUY"} onClick={() => setSide("BUY")}>
            BUY
          </button>
          <button type="button" className={`side sell${side === "SELL" ? " on" : ""}`} aria-pressed={side === "SELL"} onClick={() => setSide("SELL")}>
            SELL
          </button>
        </div>

        <div className="ticket-field">
          <span aria-hidden="true">Lots</span>
          <span className="stepper">
            <button type="button" aria-label="One lot less" onClick={() => setParams({ lots: Math.max(0, lots - 1) })}>
              −
            </button>
            <input
              name="lots"
              aria-label="Lots"
              inputMode="numeric"
              value={lots}
              onChange={(e) => setParams({ lots: Math.max(0, Math.floor(Number(e.target.value.replace(/\D/g, "")) || 0)) })}
            />
            <button type="button" aria-label="One lot more" onClick={() => setParams({ lots: lots + 1 })}>
              +
            </button>
          </span>
          <span className="dim">= {fmtQty(qty)}</span>
        </div>

        <div className="ticket-types" role="group" aria-label="Order type">
          {TICKET_TYPES.map((t) => (
            <button key={t} type="button" className={`book-tab${type === t ? " on" : ""}`} aria-pressed={type === t} onClick={() => setParams({ type: t })}>
              {t}
            </button>
          ))}
        </div>

        <PriceField label="Price" name="price" value={priceText} onChange={setPriceText} disabled={!needsPrice(type)} onStep={(n) => nudge("price", n)} />
        <PriceField label="Trigger" name="trigger" value={triggerText} onChange={setTriggerText} disabled={!needsTrigger(type)} onStep={(n) => nudge("trigger", n)} />

        <label className="ticket-field">
          TIF
          <select value={tif} onChange={(e) => setParams({ tif: e.target.value as Tif })}>
            <option>DAY</option>
            <option>IOC</option>
          </select>
        </label>
        <label className="ticket-field">
          Prod
          <select value={product} onChange={(e) => setParams({ product: e.target.value })}>
            {products.map((p) => (
              <option key={p}>{p}</option>
            ))}
          </select>
        </label>
        <label className="ticket-field">
          Acct
          <select value={accountId ?? ""} onChange={(e) => setParams({ accountId: e.target.value })}>
            {(me?.accounts ?? []).map((a) => (
              <option key={a.id} value={a.id}>
                {a.id}
                {a.can_trade ? "" : " (view)"}
              </option>
            ))}
          </select>
        </label>
        <label className="ticket-field" title="Orders worth more than this need a second click">
          Confirm over
          <select value={params.confirmOverPaise ?? 0} onChange={(e) => setParams({ confirmOverPaise: Number(e.target.value) })}>
            {CONFIRM_CHOICES.map(([v, label]) => (
              <option key={v} value={v}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="ticket-summary" aria-label="Order summary">
        <span>
          Value <b>{summary.value !== null ? rupees(summary.value) : "–"}</b>
        </span>
        <span>
          Worst position{" "}
          <b className={summary.worstPosition > 0 ? "buy-text" : summary.worstPosition < 0 ? "sell-text" : ""}>
            {summary.worstPosition > 0 ? `+${fmtQty(summary.worstPosition)}` : fmtQty(summary.worstPosition)}
          </b>
        </span>
        {summary.worstPrice !== null && (
          <span title="Market orders carry automatic protection: they never trade beyond this price">
            {side === "BUY" ? "Pays at most" : "Gets at least"} <b>{fmtPrice(summary.worstPrice)}</b>
          </span>
        )}
        {summary.distancePct !== null && (
          <span>
            {summary.distancePct >= 0 ? "+" : "−"}
            {Math.abs(summary.distancePct).toFixed(2)}% from last
          </span>
        )}
      </div>

      <div className="ticket-actions">
        {armed ? (
          <>
            <button type="button" className={`ticket-send confirm ${side === "BUY" ? "buy" : "sell"}`} onClick={send}>
              CONFIRM {side} {fmtQty(qty)}
            </button>
            <button type="button" ref={backRef} className="btn" onClick={() => setArmed(false)}>
              Back
            </button>
          </>
        ) : (
          <button
            type="button"
            className={`ticket-send ${side === "BUY" ? "buy" : "sell"}`}
            disabled={!!blocked || !!problem}
            title={blocked ?? problem ?? "Enter"}
            onClick={send}
          >
            {what}
          </button>
        )}
      </div>

      <div className="ladder-status" role="status">
        <span className={`dot ${blocked || problem || note?.level === "WARN" ? "WARN" : "INFO"}`} />
        <span className="text">{blocked ? `Trading paused: ${blocked}` : problem ? `Fix: ${problem}` : (note?.text ?? "Enter sends · Esc clears · ↑↓ one tick")}</span>
      </div>
    </div>
  );
}

function PriceField(props: { label: string; name: string; value: string; disabled: boolean; onChange: (v: string) => void; onStep: (ticks: number) => void }) {
  return (
    <div className={`ticket-field${props.disabled ? " off" : ""}`}>
      <span aria-hidden="true">{props.label}</span>
      <span className="stepper">
        <button type="button" aria-label={`${props.label} one tick lower`} disabled={props.disabled} onClick={() => props.onStep(-1)}>
          −
        </button>
        <input name={props.name} aria-label={props.label} inputMode="decimal" value={props.disabled ? "" : props.value} disabled={props.disabled} onChange={(e) => props.onChange(e.target.value)} />
        <button type="button" aria-label={`${props.label} one tick higher`} disabled={props.disabled} onClick={() => props.onStep(1)}>
          +
        </button>
      </span>
    </div>
  );
}

registerWidget<TicketParams>({
  kind: "ticket",
  title: "Order Ticket",
  description: "Any order type (LMT, MKT, SL, SL-M) with its value and worst-case position",
  component: Ticket,
  defaults: () => ({ link: "pink", lots: 1, type: "LMT", tif: "DAY", confirmOverPaise: 0 }),
  link: "follows",
  tabTitle: (p) => (p.instrumentId ? `Ticket · ${shortName(p.instrumentId)}` : undefined),
  size: { width: 420 },
});
