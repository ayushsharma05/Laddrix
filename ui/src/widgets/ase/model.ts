// The Autospreader's form, as pure functions (#414): a draft of a spread's name and legs, the
// small conveniences (the price multiplier follows the ratio; one quoting leg), and the body
// sent to the server. The server checks every rule and says what's wrong in words.

import type { Spread, SpreadLeg } from "../../api/types";

export const MAX_LEGS = 6; // the server's limit (spreads/model.py); it says so too
export const MIN_LEGS = 2;

export interface LegDraft {
  instrumentId: string;
  ratio: number;
  multiplier: number;
  quote: boolean;
  payupTicks: number;
}

export interface Draft {
  name: string;
  legs: LegDraft[];
}

const blankLeg = (ratio: number, quote = false): LegDraft => ({ instrumentId: "", ratio, multiplier: ratio, quote, payupTicks: 2 });

/** A new spread: two legs, buy the first and sell the second (a calendar), quoting the first. */
export function emptyDraft(): Draft {
  return { name: "", legs: [blankLeg(1, true), blankLeg(-1)] };
}

export function draftOf(s: Spread): Draft {
  return {
    name: s.name,
    legs: s.legs.map((l) => ({ instrumentId: l.instrument_id, ratio: l.ratio, multiplier: l.multiplier, quote: l.quote, payupTicks: l.payup_ticks })),
  };
}

export function bodyOf(d: Draft): { name: string; legs: SpreadLeg[] } {
  return {
    name: d.name.trim(),
    legs: d.legs.map((l) => ({ instrument_id: l.instrumentId, ratio: l.ratio, multiplier: l.multiplier, quote: l.quote, payup_ticks: l.payupTicks })),
  };
}

/** What must be filled in before the server can check the rest (it says the rest in words). */
export function missing(d: Draft): string[] {
  return d.legs.flatMap((l, i) => (l.instrumentId ? [] : [`leg ${i + 1}: pick its contract`]));
}

/** Change one leg. A new ratio carries the multiplier with it while they were equal (the usual case). */
export function setLeg(d: Draft, index: number, change: Partial<LegDraft>): Draft {
  const legs = d.legs.map((l, i) => {
    if (i !== index) return l;
    const next = { ...l, ...change };
    if (change.ratio !== undefined && change.multiplier === undefined && l.multiplier === l.ratio) next.multiplier = change.ratio;
    return next;
  });
  return { ...d, legs };
}

/** Quote this leg (only one leg quotes). */
export function quoteLeg(d: Draft, index: number): Draft {
  return { ...d, legs: d.legs.map((l, i) => ({ ...l, quote: i === index })) };
}

export function addLeg(d: Draft): Draft {
  if (d.legs.length >= MAX_LEGS) return d;
  return { ...d, legs: [...d.legs, blankLeg(1)] };
}

/** Remove a leg; the quote moves to the first leg if it was on this one. */
export function removeLeg(d: Draft, index: number): Draft {
  if (d.legs.length <= MIN_LEGS) return d;
  const legs = d.legs.filter((_, i) => i !== index);
  if (!legs.some((l) => l.quote)) legs[0] = { ...legs[0]!, quote: true };
  return { ...d, legs };
}

/** "+1 NIFTY26NOVFUT −1 NIFTY26DECFUT": what the spread buys and sells, in one line. */
export function legSummary(legs: SpreadLeg[], name: (id: string) => string): string {
  return legs.map((l) => `${l.ratio > 0 ? "+" : "−"}${Math.abs(l.ratio)} ${name(l.instrument_id)}`).join(" ");
}

/** The spread's price as a formula: "NOV − DEC", "A − 2×B + C". */
export function formula(legs: SpreadLeg[], name: (id: string) => string): string {
  return legs
    .map((l, i) => {
      const m = Math.abs(l.multiplier);
      const term = `${m === 1 ? "" : `${m}×`}${name(l.instrument_id)}`;
      if (i === 0) return l.multiplier < 0 ? `−${term}` : term;
      return `${l.multiplier < 0 ? "−" : "+"} ${term}`;
    })
    .join(" ");
}
