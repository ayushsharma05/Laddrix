// The tradebook (the order book's Trades tab): every fill at the exchange today, newest first,
// from the broker. Trades from orders placed outside Laddrix (Kite web, the phone app) say so.

import { useMemo } from "react";
import type { Trade } from "../../api/types";
import { clock, price, qty, shortName } from "../../lib/format";
import { freshness, tradeRows } from "./model";
import type { usePortfolio } from "./usePortfolio";

export function TradesTable({
  portfolio,
  publish,
  open,
}: {
  portfolio: ReturnType<typeof usePortfolio>;
  publish: (instrumentId: string) => void;
  open: (kind: string, params: Record<string, unknown>) => void;
}) {
  const { accountId, snapshot, missing } = portfolio;
  const rows = useMemo(() => tradeRows((snapshot?.items ?? []) as Trade[]), [snapshot]);
  const fresh = freshness(snapshot, missing);
  return (
    <div className="book-scroll">
      <div className="grid-head book-row trades-row" role="row">
        <span>Time</span>
        <span>Contract</span>
        <span>Side</span>
        <span>Prod</span>
        <span className="num">Qty</span>
        <span className="num">Price</span>
        <span className="num">Value</span>
        <span>Order</span>
      </div>
      <div className="grid-body" role="grid" aria-label="Trades">
        {!accountId && <div className="empty-note">No account selected.</div>}
        {accountId && (missing || snapshot?.error) && <div className={`empty-note tone-${fresh.tone}`}>{fresh.text}</div>}
        {accountId && snapshot && !rows.length && !snapshot.error && <div className="empty-note">No trades today.</div>}
        {rows.map((t) => (
          <div
            key={t.trade_id}
            role="row"
            className="row book-row trades-row"
            title={`trade ${t.trade_id} · broker order ${t.broker_order_id}${t.client_order_id ? " · double-click for its audit trail" : ""}`}
            onClick={() => publish(t.instrument_id)}
            onDoubleClick={() => t.client_order_id && open("audit", { link: "none", orderId: t.client_order_id })}
          >
            <span className="dim">{t.ts ? clock(t.ts, false) : "–"}</span>
            <span>{shortName(t.instrument_id)}</span>
            <span className={t.side === "BUY" ? "buy-text" : "sell-text"}>{t.side}</span>
            <span className="dim">{t.product}</span>
            <span className="num">{qty(t.qty)}</span>
            <span className="num">{price(t.price_paise)}</span>
            <span className="num">{price(t.value_paise)}</span>
            <span className={t.client_order_id ? "" : "dim"}>{t.client_order_id || "outside Laddrix"}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
