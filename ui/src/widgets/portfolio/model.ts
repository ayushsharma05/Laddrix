// Funds, holdings and trades as screens show them. Every figure comes from the server
// (docs/design/portfolio.md); this only orders rows, adds up columns and says how old they are.

import type { Holding, PortfolioSnapshot, Trade } from "../../api/types";
import { clock } from "../../lib/format";

export interface Freshness {
  text: string;
  tone: "dim" | "bad";
  title: string;
}

/** "as of 10:31:05", or why the latest refresh failed (the last figures stay on screen). */
export function freshness(snapshot: PortfolioSnapshot | undefined, missing: string | undefined): Freshness {
  if (missing) return { text: missing, tone: "dim", title: missing };
  if (!snapshot) return { text: "loading…", tone: "dim", title: "" };
  const when = snapshot.as_of ? clock(snapshot.as_of, false) : null;
  if (snapshot.error) {
    const text = when ? `as of ${when} · couldn't refresh: ${snapshot.error}` : `couldn't read: ${snapshot.error}`;
    return { text, tone: "bad", title: snapshot.error };
  }
  return {
    text: when ? `as of ${when}` : "",
    tone: "dim",
    title: "When the broker last answered",
  };
}

export interface HoldingTotals {
  invested: number;
  value: number | null; // null while any holding has no price yet
  pnl: number | null;
  dayChange: number;
}

export function holdingRows(items: Holding[]): Holding[] {
  return [...items].sort((a, b) => a.instrument_id.localeCompare(b.instrument_id));
}

export function holdingTotals(items: Holding[]): HoldingTotals {
  let invested = 0;
  let value: number | null = 0;
  let dayChange = 0;
  for (const h of items) {
    invested += h.invested_paise;
    value = value === null || h.value_paise === null ? null : value + h.value_paise;
    dayChange += h.day_change_paise ?? 0;
  }
  return {
    invested,
    value,
    pnl: value === null ? null : value - invested,
    dayChange,
  };
}

/** P&L as a percent of what was invested: "+2.06%". */
export function pnlPct(pnl: number | null, invested: number): string {
  if (pnl === null || invested <= 0) return "";
  const pct = (pnl / invested) * 100;
  const shown = Math.abs(pct).toFixed(2);
  if (shown === "0.00") return "0.00%";
  return `${pct > 0 ? "+" : "−"}${shown}%`;
}

/** Newest first, as a tradebook reads. */
export function tradeRows(items: Trade[]): Trade[] {
  return [...items].sort((a, b) => (b.ts ?? "").localeCompare(a.ts ?? "") || b.trade_id.localeCompare(a.trade_id));
}
