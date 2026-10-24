// The workspace a new user starts with: a Watch list on the left, ladders for the first
// futures in the middle, and the order book, positions and audit trail (Messages in a tab
// beside it) along the bottom, like canvas board 2.
// Only widgets that exist are placed, so this grows as widgets are added.
//
// A free page (#410) starts with the same widgets as windows (defaultCanvas); the ones that
// sit in tabs behind others on a docked page are left for Widgets ▾.

import type { DockviewApi } from "dockview-react";
import type { Instrument } from "../api/types";
import type { Instruments } from "../state/instruments";
import { GAP, type CanvasWindow } from "./canvas";
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

  // the bottom row: order book (+ algos and GTT tabs) | positions (+ holdings, funds, reports, MTM limits) | audit trail (+ messages)
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
  // holdings and funds wait in tabs beside positions (the account's money, from the broker)
  add("holdings", {}, positions ? { position: { referencePanel: positions.id, direction: "within" } } : undefined);
  add("funds", {}, positions ? { position: { referencePanel: positions.id, direction: "within" } } : undefined);
  add("reports", {}, positions ? { position: { referencePanel: positions.id, direction: "within" } } : undefined);
  add("mtm", {}, positions ? { position: { referencePanel: positions.id, direction: "within" } } : undefined);
  if (positions) {
    try {
      positions.api.setActive();
    } catch {
      // a nicety
    }
  }
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

/**
 * The first free Main page, sized for a 1600 px wide screen (bigger screens get empty space,
 * smaller ones scroll):
 *
 *   ┌watch┐┌ladder┐┌ladder┐┌ticket┐
 *   │     ││      ││      ││      │
 *   └─────┘└──────┘└──────┘└──────┘
 *   ┌order book──┐┌positions─┐┌audit trail┐
 *   └────────────┘└──────────┘└───────────┘
 */
export async function defaultCanvas(pageName: string, instruments: Instruments): Promise<CanvasWindow[]> {
  if (pageName !== "Main") return []; // other pages start empty: Widgets ▾ fills them
  const starter = await starterInstruments(instruments);
  const futures = starter.filter((i) => i.kind === "FUT" && i.tradable).slice(0, 2);
  const link: LinkGroup = "pink";
  const out: CanvasWindow[] = [];
  const put = (kind: string, params: Record<string, unknown>, x: number, y: number, w: number, h: number) => {
    const def = widget(kind);
    if (!def) return;
    out.push({ id: newPanelId(kind), kind, params: { ...def.defaults(), ...params }, x, y, w, h, z: out.length + 1 });
  };
  const top = 8;
  const tall = 520;
  let x = 8;
  const next = (w: number) => {
    const at = x;
    x += w + GAP;
    return at;
  };
  put("watch", { link, items: starter.map((i) => i.id) }, next(296), top, 296, tall);
  for (const [n, f] of futures.entries()) put("ladder", { link: n === 0 ? link : "none", instrumentId: f.id }, next(400), top, 400, tall);
  put("ticket", { link, instrumentId: futures[0]?.id }, next(380), top, 380, tall);
  const low = top + tall + GAP;
  const short = 300;
  x = 8;
  put("orders", {}, next(640), low, 640, short);
  put("positions", {}, next(520), low, 520, short);
  put("audit", {}, next(320), low, 320, short);
  return out;
}
