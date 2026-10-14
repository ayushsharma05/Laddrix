// Positions: net quantity and P&L per contract, live, with a total row and whether MockTrade's
// positions match the broker's (canvas board 2, docs/design/ui.md §4).
//
//   ✓ = broker 2m                                                     Day +₹14,812.50
//   Contract        Prod  Net    Avg        LTP        Realised   Unrealised  P&L
//   NIFTY26OCTFUT   NRML  +150   24,925.30  25,010.10  0.00       +12,720.00  +12,720.00
//   Total                                              …          …           …
//
// Click = send the contract to the link group; double-click = open a ladder on it.

import { useMemo } from "react";
import type { Position } from "../../api/types";
import { useNow, useStore } from "../../app/context";
import { price, qty, shortName } from "../../lib/format";
import { registerWidget, type BaseParams, type WidgetProps } from "../../workspace/registry";
import { averagePrice, positionRows, positionTotals, reconcileState, signedRupees } from "./model";

const EMPTY: Record<string, Position> = {};

const tone = (n: number) => (n > 0 ? "good" : n < 0 ? "bad" : "");

export function Positions({ publish, open }: WidgetProps) {
  const book = useStore((s) => (s.accountId ? s.books[s.accountId] : undefined));
  const positions = book?.positions ?? EMPTY;
  const rows = useMemo(() => positionRows(positions), [positions]);
  const totals = useMemo(() => positionTotals(rows), [rows]);
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
        <span title="Today's profit and loss on this account (realised + unrealised)">
          Day <b className={tone(book?.dayPnl ?? 0)}>{signedRupees(book?.dayPnl ?? 0)}</b>
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
        </div>
        <div className="grid-body" role="grid" aria-label="Positions">
          {!rows.length && <div className="empty-note">No positions today.</div>}
          {rows.map((p) => {
            const alert = alerts[p.instrument_id];
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
