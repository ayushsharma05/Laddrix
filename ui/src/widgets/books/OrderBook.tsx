// Order book: every order on the account, live, in tabs (canvas board 2, docs/design/ui.md §4).
//
//   [Working 3] Filled 11  Rejected 1  All 15                         Cancel all (3)
//   Time         Contract       Side  Qty  Filled  Price      Avg    Status          ×
//   10:14:52.103 NIFTY26OCTFUT  BUY    75       0  25,010.00   –     Working         ×
//
// Click a row = send its contract to the link group (the linked ladder switches); double-click =
// open its audit timeline; × = cancel. Cancels pass cancelBlock(): they stay allowed while
// trading is stopped, because getting out must always work.

import { useCallback, useMemo, useState } from "react";
import type { OrderView } from "../../api/types";
import { useApp, useStore } from "../../app/context";
import { clock, price, qty, shortName } from "../../lib/format";
import { cancelBlock } from "../../state/gate";
import { registerWidget, type BaseParams, type WidgetProps } from "../../workspace/registry";
import { TradesTable } from "../portfolio/TradesTable";
import { usePortfolio } from "../portfolio/usePortfolio";
import { cancellable, orderPrice, ordersFor, statusText, statusTone, tabCounts, type OrderTab } from "./model";

export interface OrderBookParams extends BaseParams {
  tab: OrderTab | "trades"; // Trades: the broker's tradebook (widgets/portfolio)
}

const TABS: [OrderTab, string][] = [
  ["working", "Working"],
  ["filled", "Filled"],
  ["rejected", "Rejected"],
  ["all", "All"],
];

const EMPTY: Record<string, OrderView> = {};

export function OrderBook({ params, setParams, publish, open }: WidgetProps<OrderBookParams>) {
  const { rest, store } = useApp();
  const accountId = useStore((s) => s.accountId);
  const orders = useStore((s) => (s.accountId ? s.books[s.accountId]?.orders : undefined)) ?? EMPTY;
  const tab = params.tab ?? "working";
  const orderTab: OrderTab = tab === "trades" ? "all" : tab;
  const rows = useMemo(() => ordersFor(orders, orderTab), [orders, orderTab]);
  const trades = usePortfolio("trades");
  const counts = useMemo(() => tabCounts(orders), [orders]);
  const [selected, setSelected] = useState<string | null>(null);

  const cancel = useCallback(
    (list: OrderView[]) => {
      const why = cancelBlock(store.getState(), accountId);
      if (why) {
        store.addMessage("WARN", "", `Not cancelled: ${why}`);
        return;
      }
      if (!accountId) return;
      for (const o of list) {
        rest
          .delete<OrderView>(`/api/accounts/${encodeURIComponent(accountId)}/orders/${o.request.client_order_id}`)
          .then((view) => store.applyOrders(accountId, [view]))
          .catch((e: unknown) =>
            store.addMessage("ERROR", o.request.instrument_id, `Cancel ${o.request.client_order_id} refused: ${e instanceof Error ? e.message : String(e)}`),
          );
      }
    },
    [rest, store, accountId],
  );

  // an unanswered order a person checked at the broker: not there, so never placed (#259)
  const [settling, setSettling] = useState<string | null>(null);
  const settle = useCallback(
    (o: OrderView) => {
      if (!accountId) return;
      setSettling(null);
      rest
        .post<OrderView>(`/api/accounts/${encodeURIComponent(accountId)}/orders/${o.request.client_order_id}/settle`, { confirm: true })
        .then((view) => store.applyOrders(accountId, [view]))
        .catch((e: unknown) => store.addMessage("ERROR", o.request.instrument_id, `Not settled: ${e instanceof Error ? e.message : String(e)}`));
    },
    [rest, store, accountId],
  );

  const working = useMemo(() => ordersFor(orders, "working").filter(cancellable), [orders]);

  return (
    <div className="book">
      <div className="book-bar">
        {TABS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            className={`book-tab${tab === key ? " on" : ""}`}
            aria-pressed={tab === key}
            onClick={() => setParams({ tab: key })}
          >
            {label} {counts[key]}
          </button>
        ))}
        {!trades.missing && (
          <button
            type="button"
            className={`book-tab${tab === "trades" ? " on" : ""}`}
            aria-pressed={tab === "trades"}
            title="Every fill at the exchange today, from the broker's tradebook"
            onClick={() => setParams({ tab: "trades" })}
          >
            Trades {trades.snapshot?.items.length ?? ""}
          </button>
        )}
        <span className="book-spacer" />
        <button
          type="button"
          className="btn small danger"
          disabled={!working.length}
          title="Cancel every working order on this account"
          onClick={() => cancel(working)}
        >
          Cancel all{working.length ? ` (${working.length})` : ""}
        </button>
      </div>
      {tab === "trades" ? (
        <TradesTable portfolio={trades} publish={publish} open={open} />
      ) : (
        <div className="book-scroll">
          <div className="grid-head book-row orders-row" role="row">
            <span>Time</span>
            <span>Contract</span>
            <span>Side</span>
            <span className="num">Qty</span>
            <span className="num">Filled</span>
            <span className="num">Price</span>
            <span className="num">Avg</span>
            <span>Status</span>
            <span />
          </div>
          <div className="grid-body" role="grid" aria-label="Orders">
            {!accountId && <div className="empty-note">No account selected.</div>}
            {accountId && !rows.length && <div className="empty-note">No {tab === "all" ? "" : `${TABS.find((t) => t[0] === tab)![1].toLowerCase()} `}orders today.</div>}
            {rows.map((o) => {
              const id = o.request.client_order_id;
              const status = statusText(o);
              return (
                <div
                  key={id}
                  role="row"
                  aria-selected={selected === id}
                  className={`row book-row orders-row${selected === id ? " selected" : ""}`}
                  title={`${id}${o.broker_order_id ? ` · broker ${o.broker_order_id}` : ""}${o.request.basket_id ? ` · basket ${o.request.basket_id}` : ""} · double-click for its audit trail`}
                  onClick={() => {
                    setSelected(id);
                    publish(o.request.instrument_id);
                  }}
                  onDoubleClick={() => open("audit", { link: "none", orderId: id })}
                >
                  <span className="dim">{clock(o.created_at)}</span>
                  <span>{shortName(o.request.instrument_id)}</span>
                  <span className={o.request.side === "BUY" ? "buy-text" : "sell-text"}>
                    {o.request.side}
                    {o.request.reduce_only && (
                      <span className="dim" title="A close: it can never close more than is open, so it can't reverse the position">
                        {" "}close
                      </span>
                    )}
                  </span>
                  <span className="num">{qty(o.request.qty)}</span>
                  <span className="num">{qty(o.filled_qty)}</span>
                  <span className="num">{orderPrice(o)}</span>
                  <span className="num">{o.filled_qty > 0 ? price(o.avg_price_paise) : "–"}</span>
                  <span className={`tone-${statusTone(o)}`} title={o.reason || status}>
                    {status}
                    {o.status === "REJECTED" && o.reason ? `: ${o.reason}` : ""}
                    {o.pending === "UNCONFIRMED" && !o.broker_order_id && (
                      <button
                        type="button"
                        className="btn small settle-btn"
                        title="The broker never answered. Check the broker's own order book: if the order isn't there, mark it as never placed."
                        onClick={(e) => {
                          e.stopPropagation();
                          if (settling === id) settle(o);
                          else setSettling(id);
                        }}
                        onDoubleClick={(e) => e.stopPropagation()}
                      >
                        {settling === id ? "Confirm: not in the broker's book" : "Not placed?"}
                      </button>
                    )}
                  </span>
                  {cancellable(o) ? (
                    <button
                      type="button"
                      className="row-action"
                      aria-label={`Cancel ${o.request.side} ${o.request.qty} ${shortName(o.request.instrument_id)}`}
                      title="Cancel"
                      onClick={(e) => {
                        e.stopPropagation();
                        cancel([o]);
                      }}
                      onDoubleClick={(e) => e.stopPropagation()}
                    >
                      ×
                    </button>
                  ) : (
                    <span />
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

registerWidget<OrderBookParams>({
  kind: "orders",
  title: "Order Book",
  description: "Working, filled and rejected orders; cancel; double-click for the audit trail",
  component: OrderBook,
  defaults: () => ({ link: "pink", tab: "working" }),
  link: "drives",
});
