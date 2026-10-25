// Order ticket: every order type from a form (docs/design/ui.md §4, canvas board 3).
//
//   NIFTY26OCTFUT · LTP 25,010.00 · bid 25,010.00 / ask 25,010.10
//   [ BUY ][ SELL ]   Lots [−] 2 [+] = 150      Type LMT MKT SL SL-M
//   Price  [−] 25,010.00 [+]      Trigger [−] 25,015.00 [+]     TIF  Prod  Acct
//   Value ₹37,51,500.00 · charges ≈ ₹21.40 · worst position +225 · +0.00% from last
//   [ BUY 150 NIFTY26OCTFUT @ 25,010.00 ]     → over "confirm over": [CONFIRM BUY 150] [Back]
//
// Enter sends, Esc clears, ↑/↓ move the price one tick (the defaults: the user can change them,
// K13.4, and the hint at the bottom shows theirs). The same gate as the ladder
// (tradingBlock) runs before every send, and the server checks everything again.

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { chargeLines, marginText, useChargeEstimate, useMarginCheck } from "./useChargeEstimate";
import { ApiError } from "../../api/rest";
import type { Instrument, OrderSpec, OrderView, Side } from "../../api/types";
import { useApp, useInstrument, useKeyAction, useKeyBindings, useOrderSpec, useStore, useTick } from "../../app/context";
import { keyText } from "../../keys/model";
import { ExpiryNote } from "../../components/ExpiryBadge";
import { SearchBox } from "../../components/SearchBox";
import { price as fmtPrice, qty as fmtQty, rupees, shortName } from "../../lib/format";
import { tradingBlock } from "../../state/gate";
import type { PricePick } from "../../workspace/links";
import { registerWidget, type BaseParams, type WidgetProps } from "../../workspace/registry";
import { StartForm, useAlgoSpecs } from "../algos/AlgoManager";
import { GttForm, useGttSpec } from "../gtt/GttBook";
import { CONFIRM_CHOICES, LOT_PRESETS, confirmItems } from "../ladder/Ladder";
import { leaves, snap, unconfirmed, workingOrders } from "../ladder/model";
import {
  buildTicketOrder,
  needsPrice,
  needsTrigger,
  parsePrice,
  pickType,
  pickValidity,
  stepPrice,
  summarise,
  ticketProblem,
} from "./model";

export interface TicketParams extends BaseParams {
  mode?: "order" | "algo" | "gtt"; // the ticket's tabs: a plain order, an algo, or a GTT
  lots: number;
  type: string; // an order type code from the order spec ("LIMIT", "SL-M"…)
  tif: string;
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
  return <TicketWithSpec key={id} {...props} instrument={instrument} />;
}

/** The ticket is drawn from the server's order spec: wait for it (per account + contract). */
function TicketWithSpec(props: WidgetProps<TicketParams> & { instrument: Instrument }) {
  const me = useStore((s) => s.me);
  const globalAccount = useStore((s) => s.accountId);
  const saved = props.params.accountId;
  const accountId = saved && me?.accounts.some((a) => a.id === saved) ? saved : globalAccount;
  const spec = useOrderSpec(accountId, props.instrument.id);
  if (!spec) return <div className="empty-note">Loading what can be ordered on {props.instrument.symbol}…</div>;
  if (!spec.order_types.length) return <div className="empty-note">{props.instrument.symbol} can't be traded: it has prices only.</div>;
  return <TicketBody {...props} accountId={accountId} spec={spec} />;
}

const toText = (paise: number | null) => (paise === null ? "" : fmtPrice(paise));

function TicketBody({
  params,
  setParams,
  instrument,
  accountId,
  spec,
}: WidgetProps<TicketParams> & { instrument: Instrument; accountId: string | null; spec: OrderSpec }) {
  const { rest, store } = useApp();
  const id = instrument.id;
  const tick = useTick(id);
  const me = useStore((s) => s.me);
  const orders = useStore((s) => (accountId ? s.books[accountId]?.orders : undefined));
  const positions = useStore((s) => (accountId ? s.books[accountId]?.positions : undefined));
  const blocked = useStore((s) => tradingBlock(s, accountId));
  const { specs: algoSpecs } = useAlgoSpecs(accountId);
  const { spec: gttSpec } = useGttSpec(accountId, instrument.id); // none: the broker holds no GTTs

  const type = pickType(spec, params.type)!; // TicketWithSpec makes sure there is one
  const tif = pickValidity(type, params.tif);
  const lots = Math.max(0, params.lots ?? 1);
  const products = spec.products;
  const product = params.product && products.includes(params.product) ? params.product : (products[0] ?? "");
  const tickSize = spec.tick_paise;
  const ltp = tick?.ltp_paise ?? instrument.ltp_paise ?? null;

  const [side, setSide] = useState<Side>("BUY");
  const keyAction = useKeyAction("ticket");
  const bindings = useKeyBindings();
  const k = (id: string) => keyText(id, bindings);
  const [priceText, setPriceText] = useState("");
  const [triggerText, setTriggerText] = useState("");
  // "Confirm" belongs to one exact order: armed only while nothing about it has changed.
  // (Derived, not reset by an effect, so no render can ever confirm a different order.)
  const [armedFor, setArmedFor] = useState<string | null>(null);
  const [note, setNote] = useState<{ level: "INFO" | "WARN"; text: string } | null>(null);
  const backRef = useRef<HTMLButtonElement>(null);

  // start the price at the last price once it's known (while drawing: no frame shows it empty)
  if (priceText === "" && ltp !== null) setPriceText(toText(snap(ltp, tickSize)));

  // a price picked on a linked chart: fill it in (a priced type if this one has no price);
  // a pick made just before this ticket switched to that instrument counts too
  const { links } = useApp();
  const takenPick = useRef(0);
  const link = params.link ?? "none";
  useEffect(() => {
    const take = (pick: PricePick) => {
      if (pick.instrumentId !== id || pick.seq <= takenPick.current) return;
      takenPick.current = pick.seq;
      setPriceText(toText(snap(pick.pricePaise, tickSize)));
      if (!needsPrice(type)) {
        const priced = spec.order_types.find(needsPrice);
        if (priced) setParams({ type: priced.code });
      }
      setNote({ level: "INFO", text: `Price ${fmtPrice(snap(pick.pricePaise, tickSize))} from the chart: check it, then send` });
    };
    const fresh = links.freshPick(link);
    if (fresh) take(fresh);
    return links.subscribePrice(link, take);
  }, [links, link, id, tickSize, type, spec, setParams]);

  const price = parsePrice(priceText);
  const trigger = parsePrice(triggerText);
  const input = { spec, type, symbol: instrument.symbol, side, lots, price, trigger, validity: tif, product };
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
  const qty = lots * spec.lot_size;
  const estimateFor = {
    instrumentId: id,
    side,
    qty,
    pricePaise: summary.value !== null && qty ? Math.round(summary.value / qty) : null,
    product,
  };
  const charges = useChargeEstimate(accountId, estimateFor);
  const margin = useMarginCheck(accountId, estimateFor);
  const marginLine = margin ? marginText(margin, rupees) : null;
  const what = `${side} ${fmtQty(qty)} ${instrument.symbol}${needsPrice(type) && price !== null ? ` @ ${fmtPrice(price)}` : ` ${type.label}`}${needsTrigger(type) && trigger !== null ? ` trigger ${fmtPrice(trigger)}` : ""}`;

  const signature = JSON.stringify([side, priceText, triggerText, lots, type.code, tif, product, accountId]);
  const armed = armedFor === signature;
  const setArmed = (on: boolean) => setArmedFor(on ? signature : null);
  useLayoutEffect(() => {
    if (armed) backRef.current?.focus(); // focus before paint: Enter can't confirm by accident
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
    const body = buildTicketOrder(input, id);
    store.noteSent();
    setNote({ level: "INFO", text: `${what} ${tif} sent` });
    rest
      .post<OrderView>(`/api/accounts/${encodeURIComponent(accountId)}/orders`, body)
      .then((view) => {
        store.applyOrders(accountId, [view]);
        if (view.status === "REJECTED") setNote({ level: "WARN", text: `${what} refused: ${view.reason}` });
      })
      .catch((e: unknown) =>
        warn(e instanceof ApiError && e.status === 0 ? `${what}: no answer from Laddrix, check the order book before sending again` : `${what} refused: ${e instanceof Error ? e.message : String(e)}`),
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

  const mode = params.mode ?? "order";
  const tabs = <ModeTabs mode={mode} gtt={!!gttSpec} onChange={(m) => setParams({ mode: m })} />;
  if (mode === "algo" && accountId) {
    return (
      <div className="ticket advanced">
        {tabs}
        {algoSpecs ? (
          <StartForm
            key={`${accountId}.${id}`}
            accountId={accountId}
            specs={algoSpecs}
            defaultInstrument={id}
            defaultSide={side}
            defaultLots={lots || 1}
            onStarted={(view) => {
              store.applyAlgos(accountId, [view]);
              store.addMessage("INFO", id, `${view.algo_id} ${view.code} started from the ticket`);
              setParams({ mode: "order" });
            }}
            onCancel={() => setParams({ mode: "order" })}
          />
        ) : (
          <div className="empty-note">Loading algos…</div>
        )}
      </div>
    );
  }
  if (mode === "gtt" && accountId && gttSpec) {
    return (
      <div className="ticket advanced">
        {tabs}
        <GttForm
          key={`${accountId}.${id}`}
          accountId={accountId}
          defaultInstrument={id}
          defaultSide={side}
          defaultLots={lots || 1}
          onPlaced={(view) => {
            store.applyGtts(accountId, [view]);
            setParams({ mode: "order" });
          }}
          onCancel={() => setParams({ mode: "order" })}
        />
      </div>
    );
  }

  return (
    <div
      className={`ticket ${side === "BUY" ? "buy" : "sell"}${blocked ? " blocked" : ""}`}
      onKeyDown={(e) => {
        // the user's keys for the ticket (K13.4); sending still goes through its confirm rules
        const action = keyAction(e);
        const field = e.target instanceof HTMLInputElement && e.target.name === "trigger" ? "trigger" : "price";
        if (action === "ticket-send" && !(e.target instanceof HTMLButtonElement)) send();
        else if (action === "ticket-clear") clear();
        else if (action === "ticket-buy") setSide("BUY");
        else if (action === "ticket-sell") setSide("SELL");
        else if ((action === "ticket-up" || action === "ticket-down") && !(e.target instanceof HTMLSelectElement)) nudge(field, action === "ticket-up" ? 1 : -1);
        else return;
        e.preventDefault();
      }}
    >
      {tabs}
      <ExpiryNote info={spec.expiry} />
      <div className="ticket-head">
        <b>{instrument.symbol}</b>
        <span className="dim">
          {instrument.exchange} · lot {spec.lot_size} · tick {fmtPrice(tickSize)}
          {spec.freeze_qty ? ` · max ${fmtQty(spec.freeze_qty)} per order` : ""}
        </span>
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
          {spec.order_types.map((t) => (
            <button
              key={t.code}
              type="button"
              className={`book-tab${type.code === t.code ? " on" : ""}`}
              aria-pressed={type.code === t.code}
              title={`${t.name}: ${t.help}`}
              onClick={() => setParams({ type: t.code })}
            >
              {t.label}
            </button>
          ))}
        </div>

        <PriceField label="Price" name="price" value={priceText} onChange={setPriceText} disabled={!needsPrice(type)} onStep={(n) => nudge("price", n)} />
        <PriceField label="Trigger" name="trigger" value={triggerText} onChange={setTriggerText} disabled={!needsTrigger(type)} onStep={(n) => nudge("trigger", n)} />

        <label className="ticket-field">
          TIF
          <select value={tif} onChange={(e) => setParams({ tif: e.target.value })}>
            {type.validities.map((v) => (
              <option key={v}>{v}</option>
            ))}
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
        <span title={charges ? chargeLines(charges, fmtPrice) : "Charges if it fills in full (brokerage, taxes, fees)"}>
          Charges ≈ <b>{charges ? rupees(charges.total_paise) : "–"}</b>
        </span>
        {marginLine && (
          <span className={marginLine.tone ? `tone-${marginLine.tone}` : ""} title={marginLine.title}>
            {marginLine.text}
          </span>
        )}
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
        <span className="text">{blocked ? `Trading paused: ${blocked}` : problem ? `Fix: ${problem}` : (note?.text ?? `${k("ticket-send")} sends · ${k("ticket-clear")} clears · ${k("ticket-up")}${k("ticket-down")} one tick`)}</span>
      </div>
    </div>
  );
}

/** Order | Algo | GTT: what the ticket sends. GTT shows only when the broker holds GTTs. */
function ModeTabs({ mode, gtt, onChange }: { mode: string; gtt: boolean; onChange: (m: "order" | "algo" | "gtt") => void }) {
  const tabs: ["order" | "algo" | "gtt", string, string][] = [
    ["order", "Order", "A single order now"],
    ["algo", "Algo", "Work the order with an algo, or protect a position (trailing stop, OCO, bracket)"],
  ];
  if (gtt) tabs.push(["gtt", "GTT", "An order the broker holds until a price is touched"]);
  return (
    <div className="ticket-modes" role="tablist" aria-label="Ticket mode">
      {tabs.map(([key, label, help]) => (
        <button key={key} type="button" role="tab" aria-selected={mode === key} className={`book-tab${mode === key ? " on" : ""}`} title={help} onClick={() => onChange(key)}>
          {label}
        </button>
      ))}
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
        <input name={props.name} aria-label={props.label} inputMode="decimal" value={props.disabled ? "" : props.value}
          disabled={props.disabled}
          onChange={(e) => props.onChange(e.target.value)}
          onBlur={() => {
            const paise = parsePrice(props.value); // "25178.6" -> "25,178.60" once typed
            if (paise !== null) props.onChange(fmtPrice(paise));
          }}
        />
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
  description: "Every order type the broker offers, with its value and worst-case position",
  component: Ticket,
  defaults: () => ({ link: "pink", lots: 1, type: "LIMIT", tif: "DAY", confirmOverPaise: 0 }),
  link: "follows",
  tabTitle: (p) => (p.instrumentId ? `Ticket · ${shortName(p.instrumentId)}` : undefined),
  size: { width: 420 },
  menu: (p, act) => [
    {
      label: "Show",
      items: (
        [
          ["order", "Order"],
          ["algo", "Algo"],
          ["gtt", "GTT"],
        ] as const
      ).map(([mode, label]) => ({ label, checked: (p.mode ?? "order") === mode, onSelect: () => act.setParams({ mode }) })),
    },
    { label: "Lots", items: LOT_PRESETS.map((n) => ({ label: String(n), checked: p.lots === n, onSelect: () => act.setParams({ lots: n }) })) },
    { label: "Confirm orders over", items: confirmItems(p.confirmOverPaise, (v) => act.setParams({ confirmOverPaise: v })) },
  ],
});
