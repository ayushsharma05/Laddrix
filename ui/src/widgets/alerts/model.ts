// Alerts manager logic (K12.4): the create request from the server's alert spec, what an
// alert says in words, and its state's tone. The kinds and their settings come from the server
// (GET /api/alerts/spec, the algo-spec format): nothing here knows a kind.

import type { AlertSpec, AlgoParamSpec, AlgoSpec, MarketAlert } from "../../api/types";
import { price, qty, shortName } from "../../lib/format";
import { parseParam, type FormValues } from "../algos/model";

export interface AlertInput {
  kind: AlgoSpec | undefined;
  instrumentId: string | null;
  values: FormValues;
  repeat: string;
  cooldown: string; // seconds, as typed
  note: string;
  webhook: boolean;
}

export interface AlertBody {
  kind: string;
  instrument_id: string;
  params: Record<string, unknown>;
  repeat: string;
  cooldown_s: number;
  note: string;
  webhook: boolean;
}

/** The POST body, or every problem (each a sentence); the server checks everything again. */
export function buildAlertBody(spec: AlertSpec, t: AlertInput): { body: AlertBody | null; problems: string[] } {
  const problems: string[] = [];
  if (!t.kind) problems.push("Pick what to watch");
  if (!t.instrumentId) problems.push("Pick a contract");
  const params: Record<string, unknown> = {};
  for (const p of t.kind?.params ?? []) {
    const r = parseParam(p, t.values[p.name]);
    if (!r.ok) problems.push(r.problem);
    else if (!("skip" in r)) params[p.name] = r.value;
  }
  if (!spec.repeat.includes(t.repeat)) problems.push(`Repeat must be one of ${spec.repeat.join(", ")}`);
  const cooldown = Number(t.cooldown);
  if (!/^\d+$/.test(t.cooldown.trim()) || cooldown < spec.cooldown_s.min || cooldown > spec.cooldown_s.max)
    problems.push(`Cool-down must be ${spec.cooldown_s.min}–${spec.cooldown_s.max} seconds`);
  if (t.note.length > spec.note_max) problems.push(`The note can be at most ${spec.note_max} characters`);
  if (problems.length || !t.kind || !t.instrumentId) return { body: null, problems };
  return {
    body: { kind: t.kind.code, instrument_id: t.instrumentId, params, repeat: t.repeat, cooldown_s: cooldown, note: t.note.trim(), webhook: t.webhook },
    problems,
  };
}

/** One setting's value as people read it, by its kind in the spec. */
export function paramText(p: AlgoParamSpec, v: unknown): string {
  if (v === null || v === undefined) return "–";
  if ((p.kind === "price" || p.kind === "price_offset") && typeof v === "number") return price(v);
  if (p.kind === "percent" && typeof v === "number") return `${v}%`;
  if (p.kind === "count" && typeof v === "number") return qty(v);
  if (p.kind === "instrument" && typeof v === "string") return shortName(v);
  return String(v);
}

/** "Price crosses above 25,100.00", "Moves from the previous close 2% · up". */
export function describe(spec: AlertSpec, a: MarketAlert): string {
  const kind = spec.kinds.find((k) => k.code === a.kind);
  if (!kind) return a.kind;
  const parts = kind.params.map((p) => paramText(p, a.params[p.name]));
  return `${kind.name} ${parts.join(" · ")}`;
}

export type Tone = "good" | "bad" | "pending" | "dim";

/** How an alert's state reads: fired (good), watching (dim), waiting/already true (pending), off. */
export function stateTone(a: MarketAlert): Tone {
  if (!a.enabled) return "dim";
  if (a.done) return "good";
  if (a.status === "watching") return "dim";
  if (a.status.startsWith("fired")) return "good";
  return "pending";
}

/** A changed alert from the stream replaces its row; a new one is added; order by creation. */
export function upsert(list: MarketAlert[], a: MarketAlert): MarketAlert[] {
  const at = list.findIndex((x) => x.alert_id === a.alert_id);
  const next = at >= 0 ? list.map((x, k) => (k === at ? a : x)) : [...list, a];
  return next.sort((x, y) => (x.created_at < y.created_at ? -1 : x.created_at > y.created_at ? 1 : 0));
}
