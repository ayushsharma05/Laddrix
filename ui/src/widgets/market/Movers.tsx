// Movers: the biggest gainers, losers, volume and open-interest change among the contracts the
// server has prices for, ranked by the server (GET /api/market/movers), read every 2 s. The
// exchange and kind filters are the ones the server says it has. Click a row: the link group
// switches to it.
//
//   [Gainers] Losers  Volume  OI     Exchange [all ▾]  Kind [all ▾]
//   RELIANCE   1,402.00   +2.10%   5.1M

import { useEffect, useRef, useState } from "react";
import type { MoversAnswer } from "../../api/types";
import { useApp } from "../../app/context";
import { price, shortVolume } from "../../lib/format";
import { registerWidget, type BaseParams, type WidgetProps } from "../../workspace/registry";

export interface MoversParams extends BaseParams {
  by?: MoversAnswer["by"];
  exchange?: string;
  kind?: string;
}

export const MOVERS_POLL_MS = 2000;
const TABS: [MoversAnswer["by"], string][] = [
  ["gainers", "Gainers"],
  ["losers", "Losers"],
  ["volume", "Volume"],
  ["oi", "OI"],
];

const pct = (n: number | null) => (n === null ? "–" : `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n).toFixed(2)}%`);
const tone = (n: number | null) => (n === null || n === 0 ? "" : n > 0 ? "good" : "bad");

export function Movers({ params, setParams, publish }: WidgetProps<MoversParams>) {
  const { rest } = useApp();
  const by = params.by ?? "gainers";
  const [answer, setAnswer] = useState<MoversAnswer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [round, setRound] = useState(0);
  const query = new URLSearchParams({ by, limit: "20", exchange: params.exchange ?? "", kind: params.kind ?? "" }).toString();

  const asked = useRef("");
  useEffect(() => {
    let live = true;
    const t = setTimeout(
      () => {
        asked.current = query;
        rest
          .get<MoversAnswer>(`/api/market/movers?${query}`)
          .then((a) => {
            if (!live) return;
            setAnswer(a);
            setError(null);
          })
          .catch((e: unknown) => live && setError(e instanceof Error ? e.message : String(e)))
          .finally(() => live && setRound((n) => n + 1));
      },
      asked.current === query ? MOVERS_POLL_MS : 0, // a new tab or filter asks at once
    );
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [rest, query, round]);

  const shown = answer?.by === by ? answer : null;
  return (
    <div className="book movers">
      <div className="book-bar">
        {TABS.map(([code, label]) => (
          <button key={code} type="button" className={`book-tab${by === code ? " on" : ""}`} aria-pressed={by === code} onClick={() => setParams({ by: code })}>
            {label}
          </button>
        ))}
        <select aria-label="Exchange" value={params.exchange ?? ""} onChange={(e) => setParams({ exchange: e.target.value })}>
          <option value="">all exchanges</option>
          {answer?.exchanges.map((x) => (
            <option key={x}>{x}</option>
          ))}
        </select>
        <select aria-label="Kind" value={params.kind ?? ""} onChange={(e) => setParams({ kind: e.target.value })}>
          <option value="">all kinds</option>
          {answer?.kinds.map((x) => (
            <option key={x}>{x}</option>
          ))}
        </select>
        <span className="book-spacer" />
        {shown && <span className="dim">of {shown.of} with prices</span>}
      </div>
      {error && (
        <div className="empty-note tone-bad" role="alert">
          {error}
        </div>
      )}
      {shown?.note && <div className="empty-note dim">{shown.note}</div>}
      <div className="grid-head book-row movers-row" role="row">
        <span>Contract</span>
        <span className="num">Last</span>
        <span className="num">Change</span>
        <span className="num">{by === "oi" ? "OI change" : "Volume"}</span>
      </div>
      <div className="grid-body book-scroll" role="grid" aria-label="Movers">
        {shown && !shown.rows.length && <div className="empty-note">Nothing to show here yet.</div>}
        {shown?.rows.map((r) => (
          <div key={r.instrument_id} role="row" className="row book-row movers-row" title={r.instrument_id} onClick={() => publish(r.instrument_id)}>
            <span>{r.symbol}</span>
            <span className="num">{price(r.ltp_paise)}</span>
            <span className={`num ${tone(r.change_pct)}`}>{pct(r.change_pct)}</span>
            <span className={`num ${by === "oi" ? tone(r.oi_change_pct) : ""}`}>{by === "oi" ? pct(r.oi_change_pct) : r.volume === null ? "–" : shortVolume(r.volume)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

registerWidget<MoversParams>({
  kind: "movers",
  title: "Movers",
  description: "Top gainers, losers, volume and open-interest change, ranked by the server",
  component: Movers,
  defaults: () => ({ link: "pink", by: "gainers" }),
  link: "drives",
});
