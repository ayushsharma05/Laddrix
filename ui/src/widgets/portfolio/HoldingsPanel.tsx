// Holdings: delivery shares kept from earlier days, at live prices, with P&L and the day's
// change, and a total row (the broker's holdings; today's delivery buys are positions).
//
//   Instrument  Qty       Avg       LTP       Invested     Value        P&L              Day
//   RELIANCE    12 (2 T1) 2,450.56  2,501.10  29,406.72    30,013.20    +606.48 +2.06%   +133.20
//
// Click = send the instrument to the link group; double-click = open a ladder on it.

import { useMemo } from "react";
import type { Holding } from "../../api/types";
import { price, qty, shortName } from "../../lib/format";
import { registerWidget, type BaseParams, type WidgetProps } from "../../workspace/registry";
import { signedRupees } from "../books/model";
import { freshness, holdingRows, holdingTotals, pnlPct } from "./model";
import { usePortfolio } from "./usePortfolio";

const tone = (n: number | null) => (n === null ? "dim" : n > 0 ? "good" : n < 0 ? "bad" : "");
const money = (n: number | null) => (n === null ? "–" : price(n));
const signed = (n: number | null) => (n === null ? "–" : signedRupees(n));

export function HoldingsPanel({ publish, open }: WidgetProps) {
  const { accountId, snapshot, missing, refreshing, refresh } = usePortfolio("holdings");
  const fresh = freshness(snapshot, missing);
  const rows = useMemo(() => holdingRows((snapshot?.items ?? []) as Holding[]), [snapshot]);
  const totals = useMemo(() => holdingTotals(rows), [rows]);
  return (
    <div className="book">
      <div className="book-bar">
        <span className={`tone-${fresh.tone}`} title={fresh.title}>
          {fresh.text}
        </span>
        <span className="book-spacer" />
        {rows.length > 0 && (
          <span title="Total profit and loss on holdings (since bought)">
            P&amp;L <b className={tone(totals.pnl)}>{signed(totals.pnl)}</b> {pnlPct(totals.pnl, totals.invested)}
          </span>
        )}
        <button type="button" className="btn small" disabled={!accountId || !!missing || refreshing} onClick={refresh} title="Ask the broker now">
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      <div className="book-scroll">
        <div className="grid-head book-row holdings-row" role="row">
          <span>Instrument</span>
          <span className="num">Qty</span>
          <span className="num">Avg</span>
          <span className="num">LTP</span>
          <span className="num">Invested</span>
          <span className="num">Value</span>
          <span className="num">P&amp;L</span>
          <span className="num">Day</span>
        </div>
        <div className="grid-body" role="grid" aria-label="Holdings">
          {!accountId && <div className="empty-note">No account selected.</div>}
          {accountId && snapshot && !rows.length && <div className="empty-note">No holdings.</div>}
          {rows.map((h) => (
            <div
              key={h.instrument_id}
              role="row"
              className="row book-row holdings-row"
              title={`${h.instrument_id}${h.used_qty ? ` · ${h.used_qty} sold today` : ""} · double-click for a ladder`}
              onClick={() => publish(h.instrument_id)}
              onDoubleClick={() => open("ladder", { instrumentId: h.instrument_id, link: "none" })}
            >
              <span>{shortName(h.instrument_id)}</span>
              <span className="num">
                {qty(h.total_qty)}
                {h.t1_qty ? <span className="dim"> ({qty(h.t1_qty)} T1)</span> : null}
                {h.used_qty ? <span className="sell-text"> −{qty(h.used_qty)}</span> : null}
              </span>
              <span className="num">{price(h.avg_price_paise)}</span>
              <span className="num">{money(h.ltp_paise)}</span>
              <span className="num">{price(h.invested_paise)}</span>
              <span className="num">{money(h.value_paise)}</span>
              <span className={`num ${tone(h.pnl_paise)}`}>
                {signed(h.pnl_paise)} <span className="dim">{pnlPct(h.pnl_paise, h.invested_paise)}</span>
              </span>
              <span className={`num ${tone(h.day_change_paise)}`}>{signed(h.day_change_paise)}</span>
            </div>
          ))}
          {rows.length > 0 && (
            <div role="row" className="row book-row holdings-row total">
              <span>Total</span>
              <span />
              <span />
              <span />
              <span className="num">{price(totals.invested)}</span>
              <span className="num">{money(totals.value)}</span>
              <span className={`num ${tone(totals.pnl)}`}>{signed(totals.pnl)}</span>
              <span className={`num ${tone(totals.dayChange)}`}>{signedRupees(totals.dayChange)}</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

registerWidget<BaseParams>({
  kind: "holdings",
  title: "Holdings",
  description: "Delivery holdings at live prices: P&L and the day's change",
  component: HoldingsPanel,
  defaults: () => ({ link: "pink" }),
  link: "drives",
});
