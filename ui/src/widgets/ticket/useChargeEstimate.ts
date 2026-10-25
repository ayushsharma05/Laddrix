// What an order would pay in charges, and the margin it would block, if it filled in full:
// asked from the server (it holds the rates and asks the broker), a moment after the trader
// stops changing the order.

import { useEffect, useState } from "react";
import type { ChargeBill, MarginCheck, Side } from "../../api/types";
import { useApp } from "../../app/context";

export const ESTIMATE_DELAY_MS = 250;

interface EstimateOrder {
  instrumentId: string;
  side: Side;
  qty: number;
  pricePaise: number | null;
  product: string;
}

export function useChargeEstimate(accountId: string | null, order: EstimateOrder): ChargeBill | null {
  return useEstimate<ChargeBill>(accountId, "charges/estimate", order);
}

export function useMarginCheck(accountId: string | null, order: EstimateOrder): MarginCheck | null {
  return useEstimate<MarginCheck>(accountId, "margin", order);
}

function useEstimate<T>(accountId: string | null, path: string, order: EstimateOrder): T | null {
  const { rest } = useApp();
  const { instrumentId, side, qty, pricePaise, product } = order;
  // kept with the order it is for: never the charges of a different order
  const key = JSON.stringify([accountId, path, instrumentId, side, qty, pricePaise, product]);
  const [bill, setBill] = useState<{ key: string; bill: T } | null>(null);
  useEffect(() => {
    if (!accountId || !qty || !pricePaise || pricePaise <= 0 || !product) return;
    let live = true;
    const timer = setTimeout(() => {
      rest
        .post<T>(`/api/accounts/${encodeURIComponent(accountId)}/${path}`, {
          instrument_id: instrumentId,
          side,
          qty,
          price_paise: pricePaise,
          product,
        })
        .then((b) => {
          if (live) setBill({ key, bill: b });
        })
        .catch(() => undefined); // a nicety: the order can still be sent
    }, ESTIMATE_DELAY_MS);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [rest, key, accountId, path, instrumentId, side, qty, pricePaise, product]);
  return bill && bill.key === key ? bill.bill : null;
}

/** "Brokerage 20.00 · STT 9.41 · …" for a tooltip (lines that cost something). */
export function chargeLines(bill: ChargeBill, fmt: (paise: number) => string): string {
  const parts = bill.lines.filter((l) => l.paise).map((l) => `${l.label} ${fmt(l.paise)}`);
  return `${parts.join(" · ") || "No charges"} (${bill.schedule.name}, rates checked ${bill.schedule.checked_on})`;
}

/** The ticket's margin line: "Margin ≈ ₹2,25,079.20 of ₹7,74,913.30", and its tone. */
export function marginText(m: MarginCheck, fmt: (paise: number) => string): { text: string; tone: "" | "bad" | "dim"; title: string } {
  if (m.exit) return { text: "Margin: none (an exit)", tone: "dim", title: "It only reduces the position: never checked" };
  if (m.required_paise === null) return { text: "Margin: –", tone: "dim", title: m.note };
  const of = m.available_paise !== null ? ` of ${fmt(m.available_paise)}` : "";
  const refused = m.enough === false && m.checked_before_sending;
  const title =
    m.enough === false
      ? `Needs more than is available${refused ? ": it will be refused before sending" : ": the broker may reject it"}`
      : (m.note || "What the broker would block for it, against the funds available");
  return { text: `Margin ≈ ${fmt(m.required_paise)}${of}`, tone: m.enough === false ? "bad" : "", title };
}
