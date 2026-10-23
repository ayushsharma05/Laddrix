// Watch list: instruments with live last price and change. Click a row to send it to the
// link group (the linked ladder and ticket switch); double-click to open a ladder on it.
//
//   row ─► useTick(id) ─► LTP, change, % (green +, red −), a short flash on each up/down tick

import { useEffect, useRef, useState } from "react";
import { useInstrument, useTick } from "../app/context";
import { ExpiryBadge } from "../components/ExpiryBadge";
import { SearchBox } from "../components/SearchBox";
import { change, changePct, price, shortName, shortVolume } from "../lib/format";
import { registerWidget, type BaseParams, type WidgetProps } from "../workspace/registry";

export interface WatchParams extends BaseParams {
  items: string[];
}

export const MAX_WATCH_ITEMS = 100;

export function Watch({ params, setParams, publish, open }: WidgetProps<WatchParams>) {
  const items = params.items ?? [];
  const [selected, setSelected] = useState<string | null>(null);

  const select = (id: string) => {
    setSelected(id);
    publish(id);
  };
  const remove = (id: string) => setParams({ items: items.filter((i) => i !== id) });
  // expired contracts leave the list (#290); several can end at once, so all go together
  const ended = useRef(new Set<string>());
  const expire = (id: string) => {
    ended.current.add(id);
    setParams({ items: items.filter((i) => !ended.current.has(i)) });
  };

  return (
    <div className="watch" tabIndex={0} onKeyDown={(e) => {
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      e.preventDefault();
      const at = selected ? items.indexOf(selected) : -1;
      const next = items[e.key === "ArrowDown" ? Math.min(at + 1, items.length - 1) : Math.max(at - 1, 0)];
      if (next) select(next);
    }}>
      <div className="grid-head watch-row">
        <span>Instrument</span>
        <span className="num">LTP</span>
        <span className="num">Chg</span>
        <span className="num">%</span>
        <span className="num wide">Bid</span>
        <span className="num wide">Ask</span>
        <span className="num wide">Volume</span>
        <span />
      </div>
      <div className="grid-body">
        {items.length === 0 && <div className="empty-note">Add instruments with the search box below.</div>}
        {items.map((id) => (
          <WatchRow
            key={id}
            id={id}
            selected={id === selected}
            onSelect={() => select(id)}
            onOpen={() => open("ladder", { instrumentId: id, link: "none" })}
            onRemove={() => remove(id)}
            onExpired={() => expire(id)}
          />
        ))}
      </div>
      <SearchBox
        className="watch-add"
        placeholder="Add symbol…"
        ariaLabel="Add to watch list"
        onPick={(i) => {
          if (!items.includes(i.id) && items.length < MAX_WATCH_ITEMS) setParams({ items: [...items, i.id] });
        }}
      />
    </div>
  );
}

interface RowProps {
  id: string;
  selected: boolean;
  onSelect: () => void;
  onOpen: () => void;
  onRemove: () => void;
  onExpired?: () => void; // the server says the contract has ended (#290)
}

export function WatchRow({ id, selected, onSelect, onOpen, onRemove, onExpired }: RowProps) {
  const tick = useTick(id);
  const instrument = useInstrument(id);
  const flash = useFlash(tick?.ltp_paise);
  const close = tick?.close_paise;
  const up = tick && close != null ? tick.ltp_paise >= close : null;
  const bid = tick?.bids[0];
  const ask = tick?.asks[0];
  const expired = instrument?.expiry_info?.expired === true;
  useEffect(() => {
    if (expired) onExpired?.(); // once, when the server says it has ended
  }, [expired]); // eslint-disable-line react-hooks/exhaustive-deps -- once per change
  return (
    <div
      className={`watch-row row${selected ? " selected" : ""}`}
      role="row"
      aria-selected={selected}
      onClick={onSelect}
      onDoubleClick={onOpen}
      title={instrument ? `${instrument.exchange}:${instrument.symbol} · lot ${instrument.lot_size} · tick ${price(instrument.tick_paise)}` : id}
    >
      <span className="name">
        <ExpiryBadge info={instrument?.expiry_info} compact />
        {instrument?.symbol ?? shortName(id)}
      </span>
      <span className={`num ltp ${flash}`}>{tick ? price(tick.ltp_paise) : "–"}</span>
      <span className={`num ${up === null ? "" : up ? "good" : "bad"}`}>{tick ? change(tick.ltp_paise, close) : ""}</span>
      <span className={`num ${up === null ? "" : up ? "good" : "bad"}`}>{tick ? changePct(tick.ltp_paise, close) : ""}</span>
      <span className="num wide buy-text">{bid ? price(bid[0]) : ""}</span>
      <span className="num wide sell-text">{ask ? price(ask[0]) : ""}</span>
      <span className="num wide dim">{tick?.volume != null ? shortVolume(tick.volume) : ""}</span>
      <button
        type="button"
        className="row-remove"
        aria-label={`Remove ${instrument?.symbol ?? id}`}
        title="Remove"
        onClick={(e) => {
          e.stopPropagation();
          onRemove();
        }}
      >
        ×
      </button>
    </div>
  );
}

/** "up" or "down" for 400 ms after the value moves, else "". */
export function useFlash(value: number | undefined): string {
  const previous = useRef(value);
  const [flash, setFlash] = useState("");
  useEffect(() => {
    const before = previous.current;
    previous.current = value;
    if (value === undefined || before === undefined || value === before) return;
    setFlash(value > before ? "flash-up" : "flash-down");
    const timer = setTimeout(() => setFlash(""), 400);
    return () => clearTimeout(timer);
  }, [value]);
  return flash;
}

registerWidget<WatchParams>({
  kind: "watch",
  title: "Watch",
  description: "Instruments with live price and change; click to link, double-click for a ladder",
  component: Watch,
  defaults: () => ({ link: "pink", items: [] }),
  link: "drives",
  size: { width: 300 },
});
