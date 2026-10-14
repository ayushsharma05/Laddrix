// Option chain logic, kept out of the widget so it can be tested on its own. The server sends
// every number (IV, Greeks, ATM, PCR…); this only decides how to show them.

import type { ChainLeg, ChainRow, OptionChain } from "../../api/types";
import { price, shortVolume } from "../../lib/format";

export type ColumnKey = "oi" | "oi_change" | "volume" | "iv" | "delta" | "gamma" | "theta" | "vega" | "bid" | "ask" | "ltp";

export interface Column {
  key: ColumnKey;
  label: string;
  title: string;
  value: (leg: ChainLeg) => number | null;
  format: (v: number) => string;
}

const signed = (text: string, v: number) => (v > 0 ? `+${text}` : v < 0 ? `−${text.replace("-", "")}` : text);

export const COLUMNS: Column[] = [
  { key: "oi", label: "OI", title: "Open interest (contracts)", value: (l) => l.oi, format: (v) => shortVolume(v) },
  { key: "oi_change", label: "Chg OI", title: "Change in OI since the first seen today", value: (l) => l.oi_change, format: (v) => signed(shortVolume(Math.abs(v)), v) },
  { key: "volume", label: "Vol", title: "Volume today", value: (l) => l.volume, format: (v) => shortVolume(v) },
  { key: "iv", label: "IV", title: "Implied volatility, % a year", value: (l) => l.iv, format: (v) => (v * 100).toFixed(2) },
  { key: "delta", label: "Δ", title: "Delta: change per ₹1 of the future", value: (l) => l.delta, format: (v) => v.toFixed(2) },
  { key: "gamma", label: "Γ", title: "Gamma: change in delta per ₹1", value: (l) => l.gamma, format: (v) => v.toFixed(5) },
  { key: "theta", label: "Θ", title: "Theta: ₹ per calendar day", value: (l) => l.theta, format: (v) => v.toFixed(2) },
  { key: "vega", label: "ν", title: "Vega: ₹ per 1 point of IV", value: (l) => l.vega, format: (v) => v.toFixed(2) },
  { key: "bid", label: "Bid", title: "Best bid", value: (l) => l.bid_paise, format: price },
  { key: "ask", label: "Ask", title: "Best offer", value: (l) => l.ask_paise, format: price },
  { key: "ltp", label: "LTP", title: "Last traded price", value: (l) => l.ltp_paise, format: price },
];

export const DEFAULT_COLUMNS: ColumnKey[] = ["oi", "oi_change", "iv", "delta", "ltp"];

/** The user's columns in their order, ignoring unknown keys (a layout saved by an older version). */
export function pickColumns(keys: readonly string[] | undefined): Column[] {
  const chosen = (keys ?? DEFAULT_COLUMNS).map((k) => COLUMNS.find((c) => c.key === k)).filter((c): c is Column => !!c);
  return chosen.length ? chosen : pickColumns(DEFAULT_COLUMNS);
}

/** A cell's text: "–" when the server has no value (never a made-up 0). */
export function cell(leg: ChainLeg | null, column: Column): string {
  if (!leg) return "";
  const v = column.value(leg);
  return v === null || v === undefined ? "–" : column.format(v);
}

/** In the money: calls below the forward, puts above it. */
export function inTheMoney(row: ChainRow, side: "call" | "put", forward: number | null): boolean {
  if (forward === null) return false;
  return side === "call" ? row.strike_paise < forward : row.strike_paise > forward;
}

/** The largest OI shown on each side: OI bars are drawn relative to it. */
export function maxOi(chain: OptionChain): { call: number; put: number } {
  let call = 0;
  let put = 0;
  for (const r of chain.rows) {
    call = Math.max(call, r.call?.oi ?? 0);
    put = Math.max(put, r.put?.oi ?? 0);
  }
  return { call, put };
}

/** "27 Oct" from "2026-10-27". */
export function expiryLabel(iso: string): string {
  const d = new Date(`${iso}T00:00:00Z`);
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", timeZone: "UTC" });
}

export const strikeText = (paise: number) => (paise % 100 === 0 ? (paise / 100).toLocaleString("en-IN") : price(paise));
