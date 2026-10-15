// The workspace a new user starts with: a Watch list on the left, ladders for the first
// futures in the middle, and the order book, positions and audit trail (Messages in a tab
// beside it) along the bottom, like canvas board 2.
// Only widgets that exist are placed, so this grows as widgets are added.

import type { DockviewApi } from "dockview-react";
import type { Instrument } from "../api/types";
import type { Instruments } from "../state/instruments";
import { newPanelId } from "./controller";
import type { LinkGroup } from "./links";
import { widget } from "./registry";

/** Symbols a first watch list starts with (each is a prefix search; the first hits are kept). */
export const STARTER_SYMBOLS = ["NIFTY", "BANKNIFTY", "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "SBIN"];
const MAX_STARTER = 16;

export async function starterInstruments(instruments: Instruments): Promise<Instrument[]> {
  const results = await Promise.all(
    STARTER_SYMBOLS.map((s) => instruments.search(s, 4).catch(() => [] as Instrument[])),
  );
  const seen = new Set<string>();
  const out: Instrument[] = [];
  for (const found of results) {
    for (const i of found) {
      if (seen.has(i.id) || out.length >= MAX_STARTER) continue;
      seen.add(i.id);
      out.push(i);
    }
  }
  return out;
}

export async function buildDefault(api: DockviewApi, pageName: string, instruments: Instruments): Promise<void> {
  if (pageName !== "Main") return; // other pages start empty: Widgets ▾ fills them
  const starter = await starterInstruments(instruments);
  const futures = starter.filter((i) => i.kind === "FUT" && i.tradable);
  const link: LinkGroup = "pink";

  const add = (kind: string, params: Record<string, unknown>, place?: Record<string, unknown>) => {
    const def = widget(kind);
    if (!def) return null;
    const merged = { ...def.defaults(), ...params };
    return api.addPanel({
      id: newPanelId(kind),
      component: kind,
      params: merged,
      title: def.tabTitle?.(merged) ?? def.title,
      ...(place ?? {}),
    });
  };

  const watch = add("watch", { link, items: starter.map((i) => i.id) });
  let anchor = watch;
  const ladderIds = futures.slice(0, 2).map((i) => i.id);
  for (const [n, id] of ladderIds.entries()) {
    const ladder = add(
      "ladder",
      { link: n === 0 ? link : "none", instrumentId: id },
      anchor ? { position: { referencePanel: anchor.id, direction: "right" } } : undefined,
    );
    if (ladder) anchor = ladder;
  }
  const ticketAnchor = anchor;
  add(
    "ticket",
    { link, instrumentId: ladderIds[0] },
    ticketAnchor ? { position: { referencePanel: ticketAnchor.id, direction: "right" } } : undefined,
  );

  // the bottom row: order book (+ algos and GTT tabs) | positions | audit trail (+ messages tab)
  const book = add(
    "orders",
    {},
    watch ? { position: { direction: "below" } } : undefined,
  );
  add("algos", {}, book ? { position: { referencePanel: book.id, direction: "within" } } : undefined);
  add("gtt", {}, book ? { position: { referencePanel: book.id, direction: "within" } } : undefined);
  if (book) {
    try {
      book.api.setActive(); // the order book shows; Algos waits in the tab next to it
    } catch {
      // a nicety
    }
  }
  const positions = add("positions", {}, book ? { position: { referencePanel: book.id, direction: "right" } } : undefined);
  const audit = add("audit", {}, positions ? { position: { referencePanel: positions.id, direction: "right" } } : undefined);
  add("messages", {}, audit ? { position: { referencePanel: audit.id, direction: "within" } } : undefined);
  if (audit) {
    try {
      audit.api.setActive(); // the audit trail shows; Messages waits in the tab next to it
    } catch {
      // a nicety
    }
  }
  if (book) {
    try {
      book.group.api.setSize({ height: 260 });
    } catch {
      // sizes are a nicety
    }
  }
  if (watch) {
    try {
      watch.group.api.setSize({ width: 280 });
    } catch {
      // sizes are a nicety
    }
  }
}
