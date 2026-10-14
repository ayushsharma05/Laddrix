// Algo manager logic, kept out of the widget so it can be tested on its own.
//
// The start form is drawn from the server's algo specs (GET …/algo-specs): one input per
// parameter, chosen by the parameter's kind. Nothing here knows any particular algo; a new
// algo or a new setting needs no change in this file. The server checks every value again.

import type { AlgoParamSpec, AlgoSpec, AlgoState, AlgoView, Side } from "../../api/types";
import { price } from "../../lib/format";
import { parsePrice } from "../ticket/model";

/** What the form holds for a parameter: text for typed values, a boolean for a flag. */
export type FormValue = string | boolean;
export type FormValues = Record<string, FormValue>;

const PRICED = new Set(["price", "price_offset"]);
const WHOLE = new Set(["lots", "ticks", "count"]);
const DECIMAL = new Set(["seconds", "minutes", "percent"]);

/** The form's starting values: each parameter's default, shown the way it is typed. */
export function initialValues(spec: AlgoSpec): FormValues {
  const values: FormValues = {};
  for (const p of spec.params) {
    if (p.kind === "flag") values[p.name] = p.default === true;
    else if (p.default === null || p.default === undefined) values[p.name] = "";
    else if (PRICED.has(p.kind) && typeof p.default === "number") values[p.name] = rupeeText(p.default);
    else values[p.name] = String(p.default);
  }
  return values;
}

/** Paise as typed text: 2501000 -> "25010.00", -500 -> "-5.00". */
export function rupeeText(paise: number): string {
  const sign = paise < 0 ? "-" : "";
  return `${sign}${(Math.abs(paise) / 100).toFixed(2)}`;
}

/** A signed price: "-5", "-5.5", "12.25" -> paise; null if it isn't one. Zero is allowed. */
export function parseOffset(text: string): number | null {
  const clean = text.replace(/[,\s₹]/g, "");
  const m = /^([-+]?)(\d+)(?:\.(\d{1,2}))?$/.exec(clean);
  if (!m) return null;
  const paise = Number(m[2]) * 100 + Number((m[3] ?? "").padEnd(2, "0"));
  return m[1] === "-" ? -paise : paise;
}

export type Parsed = { ok: true; value: unknown } | { ok: true; skip: true } | { ok: false; problem: string };

/** One parameter's typed value -> the value sent to the server, or a sentence saying why not. */
export function parseParam(p: AlgoParamSpec, raw: FormValue | undefined): Parsed {
  if (p.kind === "flag") return { ok: true, value: raw === true };
  const text = typeof raw === "string" ? raw.trim() : "";
  if (!text) return p.required ? { ok: false, problem: `${p.label} is required` } : { ok: true, skip: true };
  let value: unknown;
  if (p.kind === "price") {
    value = parsePrice(text);
    if (value === null) return { ok: false, problem: `${p.label}: not a price` };
  } else if (p.kind === "price_offset") {
    value = parseOffset(text);
    if (value === null) return { ok: false, problem: `${p.label}: not a price` };
  } else if (WHOLE.has(p.kind)) {
    if (!/^-?\d+$/.test(text)) return { ok: false, problem: `${p.label} must be a whole number` };
    value = Number(text);
  } else if (DECIMAL.has(p.kind)) {
    const n = Number(text);
    if (!Number.isFinite(n)) return { ok: false, problem: `${p.label} must be a number` };
    value = n;
  } else if (p.kind === "choice") {
    if (p.choices.length && !p.choices.includes(text)) return { ok: false, problem: `${p.label} must be one of ${p.choices.join(", ")}` };
    value = text;
  } else {
    value = text; // instrument, or a kind this screen doesn't know yet: the server decides
  }
  if (typeof value === "number") {
    // limits are in the parameter's own unit (paise for prices)
    if (p.min !== null && value < p.min) return { ok: false, problem: `${p.label} must be at least ${limitText(p, p.min)}` };
    if (p.max !== null && value > p.max) return { ok: false, problem: `${p.label} must be at most ${limitText(p, p.max)}` };
  }
  return { ok: true, value };
}

function limitText(p: AlgoParamSpec, n: number): string {
  return PRICED.has(p.kind) ? price(n) : String(n);
}

export interface StartInput {
  spec: AlgoSpec;
  values: FormValues;
  instrumentId: string | null;
  side: Side;
  lots: number;
  lotSize: number;
  product: string;
}

export interface AlgoBody {
  code: string;
  instrument_id: string;
  side: Side;
  qty: number;
  product?: string;
  params: Record<string, unknown>;
}

/** The POST body, or every problem with the form (each a sentence naming the field). */
export function buildAlgoBody(t: StartInput): { body: AlgoBody | null; problems: string[] } {
  const problems: string[] = [];
  if (!t.instrumentId) problems.push("Pick a contract");
  if (!Number.isInteger(t.lots) || t.lots < 1) problems.push("Lots must be at least 1");
  const params: Record<string, unknown> = {};
  for (const p of t.spec.params) {
    const r = parseParam(p, t.values[p.name]);
    if (!r.ok) problems.push(r.problem);
    else if (!("skip" in r)) params[p.name] = r.value;
  }
  if (problems.length || !t.instrumentId) return { body: null, problems };
  const body: AlgoBody = { code: t.spec.code, instrument_id: t.instrumentId, side: t.side, qty: t.lots * t.lotSize, params };
  if (t.product) body.product = t.product;
  return { body, problems };
}

// --- the grid --------------------------------------------------------------------------------

const ACTIVE: ReadonlySet<AlgoState> = new Set<AlgoState>(["WORKING", "PAUSED", "STOPPING"]);

export const isActive = (a: AlgoView) => ACTIVE.has(a.state);
export const canPause = (a: AlgoView) => a.state === "WORKING";
export const canResume = (a: AlgoView) => a.state === "PAUSED";
export const canStop = (a: AlgoView) => a.state === "WORKING" || a.state === "PAUSED";

/** Running algos first, then finished ones; newest first within each. */
export function sortAlgos(algos: Record<string, AlgoView>): AlgoView[] {
  return Object.values(algos).sort((a, b) => {
    const live = Number(isActive(b)) - Number(isActive(a));
    return live || b.created_at.localeCompare(a.created_at);
  });
}

export function stateTone(a: AlgoView): "good" | "bad" | "buy" | "dim" {
  switch (a.state) {
    case "WORKING":
      return "buy"; // like a working order
    case "DONE":
      return "good";
    case "FAILED":
      return "bad";
    default:
      return "dim"; // paused, stopping, stopped
  }
}

export const stateText = (s: AlgoState) => s.charAt(0) + s.slice(1).toLowerCase();

/** Progress 0..1 (filled of the parent). */
export const progress = (a: AlgoView) => (a.qty > 0 ? Math.min(1, a.filled_qty / a.qty) : 0);

/** An algo's own figures (`extra`), labelled and formatted by their names' units:
 *  `…_paise` is a price, `…_ms` milliseconds, `…_pct` a percentage, `…_bps` basis points,
 *  `…_at` a time of day; the rest as they come. Empty values are left out. */
export function figures(extra: Record<string, unknown>, labels: Record<string, string> = {}): { label: string; text: string; alert: boolean }[] {
  const out: { label: string; text: string; alert: boolean }[] = [];
  for (const [key, value] of Object.entries(extra)) {
    if (value === null || value === undefined || value === "") continue;
    let name = key;
    let text: string;
    if (key.endsWith("_paise") && typeof value === "number") {
      name = key.slice(0, -"_paise".length);
      text = price(value);
    } else if (key.endsWith("_ms") && typeof value === "number") {
      name = key.slice(0, -"_ms".length);
      text = `${value} ms`;
    } else if (key.endsWith("_pct") && typeof value === "number") {
      name = key.slice(0, -"_pct".length);
      text = `${value}%`;
    } else if (key.endsWith("_bps") && typeof value === "number") {
      name = key.slice(0, -"_bps".length);
      text = `${value > 0 ? "+" : ""}${value} bps`;
    } else if (key.endsWith("_at") && typeof value === "string" && !Number.isNaN(Date.parse(value))) {
      text = new Date(value).toLocaleTimeString("en-IN", { hour12: false, timeZone: "Asia/Kolkata" });
    } else if (typeof value === "string" && value.includes(":")) {
      text = value.split(":").pop()!; // an instrument id: its symbol is enough
    } else {
      text = String(value);
    }
    const label = labels[key] ?? name.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
    out.push({ label, text, alert: name === "unhedged" && value !== 0 });
  }
  return out;
}
