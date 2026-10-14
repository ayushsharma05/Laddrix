// Find an instrument by symbol: type, arrow through the matches, Enter to pick.

import { forwardRef, useEffect, useId, useRef, useState } from "react";
import type { Instrument } from "../api/types";
import { useApp } from "../app/context";
import { price } from "../lib/format";

interface Props {
  placeholder?: string;
  onPick: (instrument: Instrument) => void;
  className?: string;
  /** Keep the text after picking (default: clear it). */
  keepText?: boolean;
  ariaLabel?: string;
}

export const SearchBox = forwardRef<HTMLInputElement, Props>(function SearchBox(
  { placeholder = "Search product", onPick, className, keepText = false, ariaLabel = "Search product" },
  ref,
) {
  const { instruments } = useApp();
  const [text, setText] = useState("");
  const [found, setFound] = useState<Instrument[]>([]);
  const [open, setOpen] = useState(false);
  const [index, setIndex] = useState(0);
  const listId = useId();
  const request = useRef(0);

  useEffect(() => {
    const q = text.trim();
    if (!q) {
      setFound([]);
      return;
    }
    const mine = ++request.current;
    const timer = setTimeout(() => {
      instruments
        .search(q, 12)
        .then((result) => {
          if (mine !== request.current) return; // a newer search is on its way
          setFound(result);
          setIndex(0);
          setOpen(true);
        })
        .catch(() => {
          if (mine === request.current) setFound([]);
        });
    }, 120);
    return () => clearTimeout(timer);
  }, [instruments, text]);

  const pick = (i: Instrument | undefined) => {
    if (!i) return;
    onPick(i);
    setOpen(false);
    if (!keepText) setText("");
  };

  return (
    <div className={`search ${className ?? ""}`}>
      <input
        ref={ref}
        type="search"
        value={text}
        placeholder={placeholder}
        aria-label={ariaLabel}
        role="combobox"
        aria-expanded={open && found.length > 0}
        aria-controls={listId}
        aria-autocomplete="list"
        autoComplete="off"
        spellCheck={false}
        onChange={(e) => setText(e.target.value.toUpperCase())}
        onFocus={() => found.length && setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 120)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setIndex((i) => Math.min(i + 1, found.length - 1));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setIndex((i) => Math.max(i - 1, 0));
          } else if (e.key === "Enter") {
            e.preventDefault();
            pick(found[index]);
          } else if (e.key === "Escape") {
            setText("");
            setOpen(false);
            e.currentTarget.blur();
          }
        }}
      />
      {open && found.length > 0 && (
        <ul className="search-results" id={listId} role="listbox">
          {found.map((i, n) => (
            <li
              key={i.id}
              role="option"
              aria-selected={n === index}
              className={n === index ? "selected" : undefined}
              onMouseDown={(e) => {
                e.preventDefault();
                pick(i);
              }}
              onMouseEnter={() => setIndex(n)}
            >
              <span className="sym">{i.symbol}</span>
              <span className="meta">
                {i.exchange} · {i.kind}
                {i.expiry ? ` · ${i.expiry}` : ""}
                {i.strike_paise ? ` · ${price(i.strike_paise)}` : ""}
                {!i.tradable ? " · view only" : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
});
