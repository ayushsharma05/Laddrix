// Market depth: the book's levels for one contract, with bars, running totals, the whole
// book's waiting quantity, the imbalance and the spread. Follows its link group.
//
//   Orders   Qty   Bid         Ask        Qty  Orders
//        2   150  25,009.90 │ 25,010.00    75       1      ← bars: share of the largest level
//   …                         (Kite sends 5 levels: the broker's limit)
//   Book: bids 9,870 · asks 8,760 · 53% bids · spread 0.10 (1 tick)

import { useInstrument, useTick } from "../../app/context";
import { SearchBox } from "../../components/SearchBox";
import { price, qty, shortName } from "../../lib/format";
import { registerWidget, type BaseParams, type WidgetProps } from "../../workspace/registry";
import { depth } from "./model";

export function DepthWindow({ params, setInstrument }: WidgetProps<BaseParams>) {
  const id = params.instrumentId;
  const instrument = useInstrument(id);
  const tick = useTick(id);
  if (!id) {
    return (
      <div className="ladder-pick">
        <div className="empty-note">Pick a contract for the depth window.</div>
        <SearchBox placeholder="Search product…" ariaLabel="Depth instrument" onPick={(i) => setInstrument(i.id)} />
      </div>
    );
  }
  const d = depth(tick, instrument?.tick_paise ?? 1);
  const rows = Math.max(d?.bids.length ?? 0, d?.asks.length ?? 0);
  return (
    <div className="book depth">
      <div className="book-bar">
        <b>{instrument?.symbol ?? shortName(id)}</b>
        {tick && <span>LTP {price(tick.ltp_paise)}</span>}
        <span className="book-spacer" />
        {d && <span className="dim">{rows} levels: what the broker sends</span>}
      </div>
      {!d && <div className="empty-note">Waiting for prices…</div>}
      {d && (
        <>
          <div className="grid-head book-row depth-row" role="row">
            <span className="num">Orders</span>
            <span className="num">Qty</span>
            <span className="num">Bid</span>
            <span className="num">Ask</span>
            <span className="num">Qty</span>
            <span className="num">Orders</span>
          </div>
          <div className="grid-body" role="grid" aria-label="Depth">
            {Array.from({ length: rows }, (_, k) => {
              const b = d.bids[k];
              const a = d.asks[k];
              return (
                <div key={k} role="row" className="row book-row depth-row">
                  <span className="num dim">{b ? b.orders : ""}</span>
                  <span className="num depth-bar bid" style={{ ["--share" as string]: b ? b.share : 0 }} title={b ? `${qty(b.cumulative)} at this price or better` : ""}>
                    {b ? qty(b.qty) : ""}
                  </span>
                  <span className="num buy-text">{b ? price(b.price) : ""}</span>
                  <span className="num sell-text">{a ? price(a.price) : ""}</span>
                  <span className="num depth-bar ask" style={{ ["--share" as string]: a ? a.share : 0 }} title={a ? `${qty(a.cumulative)} at this price or better` : ""}>
                    {a ? qty(a.qty) : ""}
                  </span>
                  <span className="num dim">{a ? a.orders : ""}</span>
                </div>
              );
            })}
          </div>
          <div className="depth-foot" aria-label="Depth totals">
            <span>
              Shown: bids <b>{qty(d.bidTotal)}</b> · asks <b>{qty(d.askTotal)}</b>
            </span>
            {d.bookBid !== null && d.bookAsk !== null && (
              <span title="All the quantity waiting in the book, not only the levels shown">
                Book: bids <b>{qty(d.bookBid)}</b> · asks <b>{qty(d.bookAsk)}</b>
              </span>
            )}
            {d.imbalance !== null && (
              <span title="The bids' share of everything waiting">
                <b className={d.imbalance >= 0.5 ? "buy-text" : "sell-text"}>{Math.round(d.imbalance * 100)}%</b> bids
              </span>
            )}
            {d.spread && (
              <span>
                Spread <b>{price(d.spread.paise)}</b> ({d.spread.ticks} tick{d.spread.ticks === 1 ? "" : "s"})
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}

registerWidget<BaseParams>({
  kind: "depth",
  title: "Depth",
  description: "The book's levels with totals, the imbalance and the spread",
  component: DepthWindow,
  defaults: () => ({ link: "pink" }),
  link: "follows",
  tabTitle: (p) => (p.instrumentId ? `Depth · ${shortName(p.instrumentId)}` : undefined),
});
