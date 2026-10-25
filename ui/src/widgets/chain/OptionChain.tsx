// Option chain: calls on the left, strikes in the middle, puts on the right, live (K8.3).
//
//   [NIFTY ▾]  27 Oct · 24 Nov                Strikes [−] 10 [+]   Columns ▾
//        OI   Chg OI    IV     Δ      LTP │  STRIKE │    LTP     Δ     IV  Chg OI    OI
//      1.1L     +12K 12.11  0.71   410.85 │  24,700 │  102.20 −0.29 12.11    −7K  1.1L
//   ▶   82K      +2K 12.00  0.51   227.50 │  25,000 │  218.20 −0.49 12.00    +1K   82K   (ATM)
//   Forward 25,009.35 · 12.7 days · ATM IV 12.00% · PCR 0.83 · Max pain 25,000
//
// Every number comes from the server's chain (options/chain.py): this screen only draws it.
// Click a call or put = send it to the link group (the linked ticket or ladder switches to
// it); Alt-click = add it as a buy leg to the strategy builder (Alt+Shift: a sell leg);
// ↑/↓ move through strikes, ←/→ switch side, Enter sends the selected one.

import { useEffect, useMemo, useRef, useState } from "react";
import type { ChainRow, OptionChain as Chain } from "../../api/types";
import { useApp } from "../../app/context";
import { ExpiryNote } from "../../components/ExpiryBadge";
import { price } from "../../lib/format";
import { registerWidget, type BaseParams, type WidgetProps } from "../../workspace/registry";
import { cell, COLUMNS, expiryLabel, inTheMoney, maxOi, pickColumns, strikeText, type Column, type ColumnKey } from "./model";

export interface ChainParams extends BaseParams {
  underlying?: string;
  expiry?: string;
  strikes: number;
  columns: ColumnKey[];
}

type Side = "call" | "put";

const NONE: string[] = []; // one empty list, the same every render

export function OptionChain({ params, setParams, publish, open }: WidgetProps<ChainParams>) {
  const { rest, stream, store } = useApp();
  const [underlyings, setUnderlyings] = useState<string[]>([]);
  // kept with the underlying they are for: another underlying's dates are never offered
  const [dates, setDates] = useState<{ underlying: string; expiries: string[] } | null>(null);
  const [chain, setChain] = useState<Chain | null>(null);
  const [selected, setSelected] = useState<{ strike: number; side: Side } | null>(null);
  const [picking, setPicking] = useState(false);
  const grid = useRef<HTMLDivElement>(null);
  const underlying = params.underlying ?? underlyings[0];
  const expiries = dates && dates.underlying === underlying ? dates.expiries : NONE;
  const expiry = params.expiry && expiries.includes(params.expiry) ? params.expiry : expiries[0];
  const strikes = params.strikes ?? 10;
  const columns = useMemo(() => pickColumns(params.columns), [params.columns]);

  useEffect(() => {
    let live = true;
    rest
      .get<string[]>("/api/options")
      .then((u) => live && setUnderlyings(u))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [rest]);

  useEffect(() => {
    if (!underlying) return;
    let live = true;
    rest
      .get<string[]>(`/api/options/${encodeURIComponent(underlying)}/expiries`)
      .then((e) => live && setDates({ underlying, expiries: e }))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [rest, underlying]);

  useEffect(() => {
    if (!underlying || !expiry) return;
    const stop = stream.subscribeChain({ underlying, expiry, strikes }, setChain);
    return () => {
      stop();
      setChain(null); // the next chain never starts with this one's rows
    };
  }, [stream, underlying, expiry, strikes]);

  const oiMax = useMemo(() => (chain ? maxOi(chain) : { call: 0, put: 0 }), [chain]);
  const centred = useRef("");
  useEffect(() => {
    const key = chain ? `${chain.underlying}|${chain.expiry}|${strikes}` : "";
    if (!chain || centred.current === key) return;
    centred.current = key; // once per chain: after that the user scrolls
    grid.current?.querySelector(".chain-row.atm")?.scrollIntoView?.({ block: "center" });
  }, [chain, strikes]);
  const rows = chain?.rows ?? [];

  const choose = (row: ChainRow, side: Side, e?: { altKey: boolean; shiftKey: boolean }) => {
    setSelected({ strike: row.strike_paise, side });
    const leg = row[side];
    if (!leg) return;
    if (e?.altKey) {
      // Alt-click: a leg for the strategy builder (Alt+Shift: a sell)
      store.sendToStrategy({ instrument_id: leg.instrument_id, side: e.shiftKey ? "SELL" : "BUY", lots: 1 });
      if (!store.hasStrategyBuilder) open("strategy", { link: "none" });
      return;
    }
    publish(leg.instrument_id);
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (!rows.length) return;
    const index = Math.max(0, rows.findIndex((r) => r.strike_paise === selected?.strike));
    const side: Side = selected?.side ?? "call";
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const next = rows[Math.min(rows.length - 1, Math.max(0, index + (e.key === "ArrowDown" ? 1 : -1)))]!;
      setSelected({ strike: next.strike_paise, side });
    } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      setSelected({ strike: rows[index]!.strike_paise, side: e.key === "ArrowLeft" ? "call" : "put" });
    } else if (e.key === "Enter" && selected) {
      const row = rows.find((r) => r.strike_paise === selected.strike);
      if (row) choose(row, selected.side);
    }
  };

  // mirrored, so the last column (LTP by default) sits next to the strike on both sides
  const putColumns = [...columns].reverse();

  return (
    <div className="chain">
      <div className="book-bar chain-bar">
        <select aria-label="Underlying" value={underlying ?? ""} onChange={(e) => setParams({ underlying: e.target.value, expiry: undefined })}>
          {underlyings.map((u) => (
            <option key={u}>{u}</option>
          ))}
        </select>
        <div className="chain-expiries" role="group" aria-label="Expiry">
          {expiries.map((e) => (
            <button key={e} type="button" className={`book-tab${e === expiry ? " on" : ""}`} aria-pressed={e === expiry} onClick={() => setParams({ expiry: e })}>
              {expiryLabel(e)}
            </button>
          ))}
        </div>
        <span className="book-spacer" />
        <span className="dim">Strikes</span>
        <div className="stepper chain-strikes">
          <button type="button" aria-label="Fewer strikes" onClick={() => setParams({ strikes: Math.max(2, strikes - 2) })}>
            −
          </button>
          <span aria-label="Strikes each side">{strikes}</span>
          <button type="button" aria-label="More strikes" onClick={() => setParams({ strikes: Math.min(40, strikes + 2) })}>
            +
          </button>
        </div>
        <button type="button" className="btn small" aria-expanded={picking} onClick={() => setPicking((v) => !v)}>
          Columns ▾
        </button>
      </div>
      <ExpiryNote info={chain?.expiry_info} />
      {picking && <ColumnPicker chosen={columns.map((c) => c.key)} onChange={(keys) => setParams({ columns: keys })} />}
      <div className="book-scroll" ref={grid} tabIndex={0} onKeyDown={onKey} aria-label="Option chain" role="grid">
        <div className="grid-head chain-head">
          <div className="chain-row" style={{ ["--cols" as string]: columns.length }} role="row">
            <span className="chain-side-head calls">CALLS</span>
            <span />
            <span className="chain-side-head puts">PUTS</span>
          </div>
          <div className="chain-row" style={{ ["--cols" as string]: columns.length }} role="row">
            <div className="chain-cells">
              {columns.map((c) => (
                <span key={c.key} className="num" title={c.title}>
                  {c.label}
                </span>
              ))}
            </div>
            <span className="chain-strike">Strike</span>
            <div className="chain-cells">
              {putColumns.map((c) => (
                <span key={c.key} className="num" title={c.title}>
                  {c.label}
                </span>
              ))}
            </div>
          </div>
        </div>
        <div className="grid-body">
          {!underlyings.length && <div className="empty-note">No options listed.</div>}
          {underlying && expiry && !chain && <div className="empty-note">Loading the {underlying} chain…</div>}
          {rows.map((row) => {
            const atm = row.strike_paise === chain?.atm_strike_paise;
            return (
              <div key={row.strike_paise} role="row" className={`chain-row${atm ? " atm" : ""}`} style={{ ["--cols" as string]: columns.length }} aria-label={`Strike ${strikeText(row.strike_paise)}${atm ? " (ATM)" : ""}`}>
                <LegCells row={row} side="call" columns={columns} chain={chain!} oiMax={oiMax.call} selected={selected} onPick={choose} />
                <span className="chain-strike">{strikeText(row.strike_paise)}</span>
                <LegCells row={row} side="put" columns={putColumns} chain={chain!} oiMax={oiMax.put} selected={selected} onPick={choose} />
              </div>
            );
          })}
        </div>
      </div>
      {chain && (
        <div className="chain-foot" aria-label="Chain summary">
          <span>
            Forward <b>{chain.forward_paise !== null ? price(chain.forward_paise) : "–"}</b>
          </span>
          <span>
            <b>{chain.days_to_expiry.toFixed(1)}</b> days
          </span>
          <span>
            ATM IV <b>{chain.atm_iv !== null ? `${(chain.atm_iv * 100).toFixed(2)}%` : "–"}</b>
          </span>
          <span>
            PCR <b>{chain.pcr !== null ? chain.pcr.toFixed(2) : "–"}</b>
          </span>
          <span>
            Max pain <b>{chain.max_pain_paise !== null ? strikeText(chain.max_pain_paise) : "–"}</b>
          </span>
          <span className="dim">Chg OI since the first OI seen today</span>
        </div>
      )}
    </div>
  );
}

function LegCells(props: {
  row: ChainRow;
  side: Side;
  columns: Column[];
  chain: Chain;
  oiMax: number;
  selected: { strike: number; side: Side } | null;
  onPick: (row: ChainRow, side: Side, e?: { altKey: boolean; shiftKey: boolean }) => void;
}) {
  const { row, side, columns, chain, oiMax, selected, onPick } = props;
  const leg = row[side];
  const itm = inTheMoney(row, side, chain.forward_paise);
  const isSelected = selected?.strike === row.strike_paise && selected.side === side;
  const share = leg?.oi && oiMax ? Math.round((100 * leg.oi) / oiMax) : 0;
  return (
    <button
      type="button"
      className={`chain-cells chain-leg ${side}${itm ? " itm" : ""}${isSelected ? " selected" : ""}`}
      style={{ ["--oi" as string]: `${share}%` }}
      disabled={!leg}
      aria-label={leg ? `${side === "call" ? "Call" : "Put"} ${strikeText(row.strike_paise)}` : undefined}
      onClick={(e) => onPick(row, side, e)}
      title="Click: to the linked ticket/ladder · Alt-click: buy leg for a strategy · Alt+Shift: sell leg"
    >
      {columns.map((c) => (
        <span key={c.key} className="num">
          {cell(leg, c)}
        </span>
      ))}
    </button>
  );
}

function ColumnPicker({ chosen, onChange }: { chosen: ColumnKey[]; onChange: (keys: ColumnKey[]) => void }) {
  return (
    <div className="chain-columns" role="group" aria-label="Columns">
      {COLUMNS.map((c) => {
        const on = chosen.includes(c.key);
        return (
          <label key={c.key} className="check" title={c.title}>
            <input
              type="checkbox"
              checked={on}
              onChange={() => onChange(on ? chosen.filter((k) => k !== c.key) : [...chosen, c.key])}
            />
            {c.label}
          </label>
        );
      })}
    </div>
  );
}

registerWidget<ChainParams>({
  kind: "chain",
  title: "Option Chain",
  description: "Calls and puts by strike with OI, IV and Greeks; click to trade",
  component: OptionChain,
  defaults: () => ({ link: "pink", strikes: 10, columns: ["oi", "oi_change", "iv", "delta", "ltp"] }),
  tabTitle: (p) => (p.underlying ? `Chain · ${p.underlying}` : "Option Chain"),
  link: "drives",
});
