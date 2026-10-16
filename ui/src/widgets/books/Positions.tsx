// Positions: net quantity and P&L per contract, live, with a total row and whether MockTrade's
// positions match the broker's (canvas board 2, docs/design/ui.md §4).
//
//   ✓ = broker 2m                       Day gross +₹14,812.50 · charges ₹66.44 · net +₹14,746.06
//   Contract        Prod  Net    Avg        LTP        Realised   Unrealised  P&L         Charges  Net P&L
//   NIFTY26OCTFUT   NRML  +150   24,925.30  25,010.10  0.00       +12,720.00  +12,720.00  66.44    +12,653.56
//   Total                                              …          …           …           …        …
//
// Charges come from the server (charges/model.py: brokerage, STT, exchange, SEBI, IPFT, stamp,
// GST, DP), worked from today's executed orders; net = gross − charges.
//
// Click = send the contract to the link group; double-click = open a ladder on it.

import { useMemo } from "react";
import type { Position } from "../../api/types";
import { useNow, useStore } from "../../app/context";
import { price, qty, shortName } from "../../lib/format";
import { alertFor } from "../../state/store";
import { registerWidget, type BaseParams, type WidgetProps } from "../../workspace/registry";
import { SquareOffButton } from "./SquareOff";
import { averagePrice, chargesFor, positionRows, positionTotals, reconcileState, signedRupees } from "./model";

const EMPTY: Record<string, Position> = {};

const tone = (n: number) => (n > 0 ? "good" : n < 0 ? "bad" : "");

export function Positions({ publish, open }: WidgetProps) {
  const book = useStore((s) => (s.accountId ? s.books[s.accountId] : undefined));
  const positions = book?.positions ?? EMPTY;
  const rows = useMemo(() => positionRows(positions), [positions]);
  const charges = book?.charges ?? null;
  const totals = useMemo(() => positionTotals(rows, charges), [rows, charges]);
  const chargesTitle = charges
    ? `${charges.lines.filter((l) => l.paise).map((l) => `${l.label} ${price(l.paise)}`).join(" · ") || "No charges yet"} (${charges.schedule.name}, rates checked ${charges.schedule.checked_on})`
    : "Charges: none yet today";
  const now = useNow(15_000);
  const alerts = book?.alerts ?? {};
  const recon = reconcileState(book?.reconcile ?? null, Object.keys(alerts).length, now);

  return (
    <div className="book">
      <div className="book-bar">
        <span className={`tone-${recon.tone}`} title={recon.title}>
          {recon.text}
        </span>
        <span className="book-spacer" />
        <SquareOffButton />
        <span title="Today's profit and loss on this account (realised + unrealised), before charges">
          Day gross <b className={tone(book?.dayPnl ?? 0)}>{signedRupees(book?.dayPnl ?? 0)}</b>
        </span>
        <span title={chargesTitle}>
          charges <b>{price(totals.charges)}</b>
        </span>
        <span title="Today's profit and loss after charges">
          net <b className={tone((book?.dayPnl ?? 0) - totals.charges)}>{signedRupees((book?.dayPnl ?? 0) - totals.charges)}</b>
        </span>
      </div>
      <div className="book-scroll">
        <div className="grid-head book-row positions-row" role="row">
          <span>Contract</span>
          <span>Prod</span>
          <span className="num">Net</span>
          <span className="num">Avg</span>
          <span className="num">LTP</span>
          <span className="num">Realised</span>
          <span className="num">Unrealised</span>
          <span className="num">P&amp;L</span>
          <span className="num">Charges</span>
          <span className="num">Net P&amp;L</span>
        </div>
        <div className="grid-body" role="grid" aria-label="Positions">
          {!rows.length && <div className="empty-note">No positions today.</div>}
          {rows.map((p) => {
            const alert = alertFor(alerts, p);
            return (
              <div
                key={`${p.instrument_id}|${p.product}`}
                role="row"
                className={`row book-row positions-row${alert ? " differs" : ""}`}
                title={alert ? alert.message : "Double-click for a ladder"}
                onClick={() => publish(p.instrument_id)}
                onDoubleClick={() => open("ladder", { instrumentId: p.instrument_id, link: "none" })}
              >
                <span>
                  {shortName(p.instrument_id)}
                  {alert ? <b className="bad"> · broker {qty(alert.broker)}</b> : null}
                </span>
                <span className="dim">{p.product}</span>
                <span className={`num ${p.qty > 0 ? "buy-text" : p.qty < 0 ? "sell-text" : "dim"}`}>
                  {p.qty > 0 ? `+${qty(p.qty)}` : qty(p.qty)}
                </span>
                <span className="num">{p.qty !== 0 ? averagePrice(p.avg_price_paise) : "–"}</span>
                <span className="num">{p.ltp_paise !== null ? price(p.ltp_paise) : "–"}</span>
                <span className={`num ${tone(p.realised_paise)}`}>{signedRupees(p.realised_paise)}</span>
                <span className={`num ${tone(p.unrealised_paise)}`}>{signedRupees(p.unrealised_paise)}</span>
                <span className={`num ${tone(p.pnl_paise)}`}>{signedRupees(p.pnl_paise)}</span>
                <span className="num dim">{price(chargesFor(p, charges))}</span>
                <span className={`num ${tone(p.pnl_paise - chargesFor(p, charges))}`}>{signedRupees(p.pnl_paise - chargesFor(p, charges))}</span>
              </div>
            );
          })}
          {rows.length > 0 && (
            <div role="row" className="row book-row positions-row total">
              <span>Total</span>
              <span />
              <span />
              <span />
              <span />
              <span className={`num ${tone(totals.realised)}`}>{signedRupees(totals.realised)}</span>
              <span className={`num ${tone(totals.unrealised)}`}>{signedRupees(totals.unrealised)}</span>
              <span className={`num ${tone(totals.pnl)}`}>{signedRupees(totals.pnl)}</span>
              <span className="num dim" title={chargesTitle}>
                {price(totals.charges)}
              </span>
              <span className={`num ${tone(totals.net)}`}>{signedRupees(totals.net)}</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

registerWidget<BaseParams>({
  kind: "positions",
  title: "Positions",
  description: "Net position and P&L per contract, and whether they match the broker",
  component: Positions,
  defaults: () => ({ link: "pink" }),
  link: "drives",
});
