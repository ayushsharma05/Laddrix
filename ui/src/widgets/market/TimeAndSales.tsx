// Time & sales: the trades between price snapshots for one contract, newest on top, with the
// side estimated from the touch before each trade (the server works them out from every tick;
// docs/design/charts.md § K12.4). Follows its link group.
//
//   Min qty [ 0 ]      shown: bought 1,500 · sold 900          (sides are an estimate)
//   11:42:03  25,010.10     75  BUY
//   11:42:02  25,009.90    150  SELL

import { useEffect, useRef, useState } from "react";
import type { Print, PrintsAnswer } from "../../api/types";
import { useApp, useInstrument, useTick } from "../../app/context";
import { SearchBox } from "../../components/SearchBox";
import { clock, price, qty, shortName } from "../../lib/format";
import { registerWidget, type BaseParams, type WidgetProps } from "../../workspace/registry";
import { filterTape, joinTape } from "./model";

export interface TapeParams extends BaseParams {
  minQty?: number;
}

export const TAPE_POLL_MS = 1000;

export function TimeAndSales(props: WidgetProps<TapeParams>) {
  const id = props.params.instrumentId;
  if (!id) {
    return (
      <div className="ladder-pick">
        <div className="empty-note">Pick a contract for time & sales.</div>
        <SearchBox placeholder="Search product…" ariaLabel="Tape instrument" onPick={(i) => props.setInstrument(i.id)} />
      </div>
    );
  }
  return <Tape key={id} {...props} instrumentId={id} />;
}

function Tape({ params, setParams, instrumentId: id }: WidgetProps<TapeParams> & { instrumentId: string }) {
  const { rest } = useApp();
  const instrument = useInstrument(id);
  useTick(id); // watching the contract is what makes the server keep its trades
  const [tape, setTape] = useState<Print[]>([]);
  const [estimate, setEstimate] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [round, setRound] = useState(0); // each answered poll starts the next one
  const newest = useRef(0);
  newest.current = tape[0]?.seq ?? 0;

  useEffect(() => {
    let live = true;
    const t = setTimeout(
      () => {
        rest
          .get<PrintsAnswer>(`/api/instruments/${encodeURIComponent(id)}/prints?after=${newest.current}&limit=300`)
          .then((a) => {
            if (!live) return;
            setTape((old) => joinTape(old, a.prints));
            setEstimate(a.estimate);
            setError(null);
          })
          .catch((e: unknown) => live && setError(e instanceof Error ? e.message : String(e)))
          .finally(() => live && setRound((n) => n + 1));
      },
      round === 0 ? 0 : TAPE_POLL_MS,
    );
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [rest, id, round]);

  const minQty = params.minQty ?? 0;
  const { rows, bought, sold } = filterTape(tape, minQty);
  return (
    <div className="book tape">
      <div className="book-bar">
        <b>{instrument?.symbol ?? shortName(id)}</b>
        <label title="Only trades of at least this quantity">
          Min qty{" "}
          <input aria-label="Min qty" inputMode="numeric" size={6} value={minQty || ""} placeholder="0" onChange={(e) => setParams({ minQty: Math.max(0, Number(e.target.value.replace(/\D/g, "")) || 0) })} />
        </label>
        <span>
          Shown: bought <b className="buy-text">{qty(bought)}</b> · sold <b className="sell-text">{qty(sold)}</b>
        </span>
        <span className="book-spacer" />
        <span className="dim" title={estimate}>
          sides estimated
        </span>
      </div>
      {error && (
        <div className="empty-note tone-bad" role="alert">
          {error}
        </div>
      )}
      <div className="grid-head book-row tape-row" role="row">
        <span>Time</span>
        <span className="num">Price</span>
        <span className="num">Qty</span>
        <span>Side</span>
      </div>
      <div className="grid-body book-scroll" role="grid" aria-label="Time and sales">
        {!rows.length && <div className="empty-note">{tape.length ? "No trades this size yet." : "Waiting for trades…"}</div>}
        {rows.map((p) => (
          <div key={p.seq} role="row" className={`row book-row tape-row${p.side === "BUY" ? " tape-buy" : p.side === "SELL" ? " tape-sell" : ""}`}>
            <span className="dim">{p.t ? clock(p.t, false) : "–"}</span>
            <span className="num">{price(p.price_paise)}</span>
            <span className="num">{qty(p.qty)}</span>
            <span className={p.side === "BUY" ? "buy-text" : p.side === "SELL" ? "sell-text" : "dim"}>{p.side || "–"}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

registerWidget<TapeParams>({
  kind: "tape",
  title: "Time & Sales",
  description: "The trades between price snapshots, newest first, with the side estimated",
  component: TimeAndSales,
  defaults: () => ({ link: "pink", minQty: 0 }),
  link: "follows",
  tabTitle: (p) => (p.instrumentId ? `T&S · ${shortName(p.instrumentId)}` : undefined),
});
