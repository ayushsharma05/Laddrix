// What an order would pay in charges if it filled in full: asked from the server (it holds
// the rates: charges/schedules.py), a moment after the trader stops changing the order.

import { useEffect, useState } from "react";
import type { ChargeBill, Side } from "../../api/types";
import { useApp } from "../../app/context";

export const ESTIMATE_DELAY_MS = 250;

export function useChargeEstimate(
  accountId: string | null,
  order: { instrumentId: string; side: Side; qty: number; pricePaise: number | null; product: string },
): ChargeBill | null {
  const { rest } = useApp();
  const [bill, setBill] = useState<ChargeBill | null>(null);
  const { instrumentId, side, qty, pricePaise, product } = order;
  useEffect(() => {
    setBill(null); // never show the charges of a different order
    if (!accountId || !qty || !pricePaise || pricePaise <= 0 || !product) return;
    let live = true;
    const timer = setTimeout(() => {
      rest
        .post<ChargeBill>(`/api/accounts/${encodeURIComponent(accountId)}/charges/estimate`, {
          instrument_id: instrumentId,
          side,
          qty,
          price_paise: pricePaise,
          product,
        })
        .then((b) => {
          if (live) setBill(b);
        })
        .catch(() => undefined); // a nicety: the order can still be sent
    }, ESTIMATE_DELAY_MS);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [rest, accountId, instrumentId, side, qty, pricePaise, product]);
  return bill;
}

/** "Brokerage 20.00 · STT 9.41 · …" for a tooltip (lines that cost something). */
export function chargeLines(bill: ChargeBill, fmt: (paise: number) => string): string {
  const parts = bill.lines.filter((l) => l.paise).map((l) => `${l.label} ${fmt(l.paise)}`);
  return `${parts.join(" · ") || "No charges"} (${bill.schedule.name}, rates checked ${bill.schedule.checked_on})`;
}
