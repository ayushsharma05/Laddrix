// GTT form rules (docs/design/gtt.md), apart from React so each can be tested on its own.
// Nothing here knows which GTT kinds exist or how many legs they have: the server's GTT spec
// says (kinds, legs, labels, tick, lot, distance). The server checks everything again; these
// only let the trader see a problem before the click, in words.

import type { GttLeg, GttSpec, GttView, Side } from "../../api/types";
import { price } from "../../lib/format";
import { snap } from "../ladder/model";
import { parsePrice } from "../ticket/model";

export interface LegForm {
  trigger: string; // rupees as typed
  price: string;
  side: Side;
}

export interface GttBody {
  instrument_id: string;
  product: string;
  kind: string;
  legs: GttLeg[];
}

/** Legs to start from: a stop (and for two legs a target) 1% either side of the last price,
 *  each limit five ticks past its trigger so it fills, all on the tick grid. */
export function defaultLegs(spec: GttSpec, kind: string, ltp: number | null, side: Side): LegForm[] {
  const count = spec.legs[kind] ?? 1;
  const t = spec.tick_paise;
  const at = (pct: number) => (ltp ? snap(ltp * (1 + pct / 100), t) : null);
  const slack = (trigger: number | null) => (trigger === null ? null : side === "SELL" ? trigger - 5 * t : trigger + 5 * t);
  const triggers = count === 2 ? [at(-1), at(1)] : [at(side === "SELL" ? -1 : 1)];
  return triggers.map((trigger) => ({
    trigger: trigger === null ? "" : price(trigger),
    price: slack(trigger) === null ? "" : price(slack(trigger)!),
    side,
  }));
}

/** Build the request, or say what's wrong with it (every problem, in words). */
export function buildGttBody(input: {
  spec: GttSpec;
  kind: string;
  legs: LegForm[];
  lots: number;
  product: string;
  ltp: number | null;
}): { body: GttBody | null; problems: string[] } {
  const { spec, kind, legs, lots, product, ltp } = input;
  const problems: string[] = [];
  if (!spec.kinds.includes(kind)) problems.push(`${kind} GTTs are not offered here`);
  if (!Number.isInteger(lots) || lots <= 0) problems.push("Lots must be a whole number above 0");
  const parsed: GttLeg[] = [];
  legs.forEach((leg, i) => {
    const name = legs.length > 1 ? `Leg ${i + 1}` : "GTT";
    const trigger = parsePrice(leg.trigger);
    const limit = parsePrice(leg.price);
    if (trigger === null) problems.push(`${name}: the trigger isn't a price`);
    else if (trigger % spec.tick_paise) problems.push(`${name}: the trigger must be on the ${price(spec.tick_paise)} grid`);
    if (limit === null) problems.push(`${name}: the price isn't a price`);
    else if (limit % spec.tick_paise) problems.push(`${name}: the price must be on the ${price(spec.tick_paise)} grid`);
    if (trigger !== null && ltp) {
      const gap = (Math.abs(trigger - ltp) / ltp) * 100;
      if (gap < spec.min_distance_pct) {
        problems.push(`${name}: the trigger is only ${gap.toFixed(2)}% from the last price (at least ${spec.min_distance_pct}%)`);
      }
    }
    if (trigger !== null && limit !== null) {
      parsed.push({ trigger_paise: trigger, price_paise: limit, side: leg.side, qty: lots * spec.lot_size });
    }
  });
  if (parsed.length === 2 && parsed[0]!.trigger_paise >= parsed[1]!.trigger_paise) {
    problems.push("Leg 1 is the lower trigger (the stop), leg 2 the higher (the target)");
  }
  if (problems.length) return { body: null, problems };
  return { body: { instrument_id: spec.instrument_id, product, kind, legs: parsed }, problems };
}

/** Newest first; active ones before finished ones. */
export function sortGtts(gtts: Record<string, GttView>): GttView[] {
  return Object.values(gtts).sort((a, b) => {
    const activeFirst = Number(b.status === "active") - Number(a.status === "active");
    return activeFirst || (b.updated_at ?? "").localeCompare(a.updated_at ?? "");
  });
}

export function gttTone(g: GttView): "buy" | "good" | "bad" | "dim" {
  if (g.status === "active") return "buy";
  if (g.status === "triggered") return g.order_id ? "good" : "buy";
  if (g.status === "rejected" || g.status === "disabled") return "bad";
  return "dim";
}

/** "SELL 75 @ 24,780.00 when 24,800.00" for each leg. */
export function legsText(g: GttView): string {
  return g.legs.map((l) => `${l.side} ${l.qty} @ ${price(l.price_paise)} when ${price(l.trigger_paise)}`).join(" · ");
}
