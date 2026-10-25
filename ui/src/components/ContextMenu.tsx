// The app's own right-click menu (#412): a list of items, some with a submenu (▸), drawn at
// the pointer and never off screen. The keyboard works it like a desktop menu:
//
//   ↑ ↓  move        → or Enter on "▸"  open the submenu        ←  back to the parent
//   Enter / Space    choose             Esc                     close
//
// A click outside, the window losing focus or a resize closes it. Items are plain data, built
// by whoever opened the menu (workspace/menus.ts), so the menu knows nothing of trading.

import { useEffect, useLayoutEffect, useRef, useState } from "react";

export type MenuItem =
  | { label: string; onSelect: () => void; disabled?: boolean; checked?: boolean; hint?: string }
  | { label: string; items: MenuItem[]; disabled?: boolean }
  | { header: string }
  | "separator";

const isSub = (i: MenuItem): i is { label: string; items: MenuItem[]; disabled?: boolean } =>
  typeof i === "object" && "items" in i;
const isAction = (i: MenuItem): i is Extract<MenuItem, { onSelect: () => void }> =>
  typeof i === "object" && "onSelect" in i;

interface Props {
  x: number;
  y: number;
  items: MenuItem[];
  onClose: () => void;
}

/** The menu at (x, y) in its window's coordinates. */
export function ContextMenu({ x, y, items, onClose }: Props) {
  const root = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;

  useEffect(() => {
    const doc = root.current?.ownerDocument ?? document;
    const view = doc.defaultView ?? window;
    const outside = (e: Event) => {
      if (root.current && !root.current.contains(e.target as Node)) close.current();
    };
    const gone = () => close.current();
    doc.addEventListener("pointerdown", outside, true);
    view.addEventListener("blur", gone);
    view.addEventListener("resize", gone);
    return () => {
      doc.removeEventListener("pointerdown", outside, true);
      view.removeEventListener("blur", gone);
      view.removeEventListener("resize", gone);
    };
  }, []);

  return (
    <div ref={root} className="context-menu-root">
      <MenuList items={items} x={x} y={y} onClose={onClose} />
    </div>
  );
}

interface ListProps {
  items: MenuItem[];
  x: number;
  y: number;
  onClose: () => void; // close the whole menu
  onBack?: () => void; // a submenu: back to its parent
}

function MenuList({ items, x, y, onClose, onBack }: ListProps) {
  const list = useRef<HTMLDivElement>(null);
  const [place, setPlace] = useState({ left: x, top: y });
  const [open, setOpen] = useState<number | null>(null); // the item whose submenu shows
  const [subAt, setSubAt] = useState({ x: 0, y: 0 });

  // never off screen: flip left or up when there is no room
  useLayoutEffect(() => {
    const el = list.current;
    if (!el) return;
    const view = el.ownerDocument.defaultView ?? window;
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    const left = x + w > view.innerWidth ? Math.max(0, view.innerWidth - w - 4) : x;
    const top = y + h > view.innerHeight ? Math.max(0, view.innerHeight - h - 4) : y;
    setPlace({ left, top });
    buttons()[0]?.focus();
    // focus once, when the list appears
  }, [x, y]);

  const buttons = () => Array.from(list.current?.querySelectorAll<HTMLButtonElement>(":scope > .cm-item:not(:disabled)") ?? []);

  const openSub = (index: number, button: HTMLElement) => {
    const r = button.getBoundingClientRect();
    setSubAt({ x: r.right - 2, y: r.top - 4 });
    setOpen(index);
  };

  const onKey = (e: React.KeyboardEvent) => {
    const all = buttons();
    const at = all.indexOf(e.target as HTMLButtonElement);
    if (at < 0) return; // a key inside the open submenu: it handles its own
    const move = (n: number) => all[(at + n + all.length) % all.length]?.focus();
    if (e.key === "ArrowDown") move(1);
    else if (e.key === "ArrowUp") move(-1);
    else if (e.key === "Home") all[0]?.focus();
    else if (e.key === "End") all[all.length - 1]?.focus();
    else if (e.key === "ArrowRight") {
      const index = Number((e.target as HTMLElement).dataset.index);
      const item = items[index];
      if (item && isSub(item)) openSub(index, e.target as HTMLElement);
    } else if (e.key === "ArrowLeft" && onBack) onBack();
    else if (e.key === "Escape") onClose();
    else if (e.key === "Tab") onClose();
    else return;
    e.preventDefault();
    e.stopPropagation();
  };

  const back = () => {
    const index = open;
    setOpen(null);
    list.current?.querySelector<HTMLButtonElement>(`:scope > [data-index="${index}"]`)?.focus();
  };

  return (
    <div ref={list} className="context-menu" role="menu" style={{ left: place.left, top: place.top }} onKeyDown={onKey}>
      {items.map((item, i) => {
        if (item === "separator") return <div key={i} className="cm-sep" role="separator" />;
        if ("header" in item)
          return (
            <div key={i} className="cm-header" role="presentation">
              {item.header}
            </div>
          );
        const sub = isSub(item);
        return (
          <button
            key={i}
            type="button"
            data-index={i}
            className={open === i ? "cm-item open" : "cm-item"}
            role={isAction(item) && item.checked !== undefined ? "menuitemcheckbox" : "menuitem"}
            aria-checked={isAction(item) && item.checked !== undefined ? item.checked : undefined}
            aria-haspopup={sub ? "menu" : undefined}
            aria-expanded={sub ? open === i : undefined}
            disabled={item.disabled}
            onPointerEnter={(e) => {
              if (sub && !item.disabled) openSub(i, e.currentTarget);
              else setOpen(null);
            }}
            onClick={(e) => {
              if (sub) {
                openSub(i, e.currentTarget);
                return;
              }
              onClose();
              item.onSelect();
            }}
          >
            <span className="cm-check">{isAction(item) && item.checked ? "✓" : ""}</span>
            <span className="cm-label">{item.label}</span>
            <span className="cm-hint">{sub ? "▸" : isAction(item) ? (item.hint ?? "") : ""}</span>
          </button>
        );
      })}
      {open !== null && isSub(items[open]!) && <MenuList items={(items[open] as { items: MenuItem[] }).items} x={subAt.x} y={subAt.y} onClose={onClose} onBack={back} />}
    </div>
  );
}
