// Admin screen logic (K13.2): the limits form from the server's limits spec, what a user's or an
// account's state reads as, and the authenticator link's parts. Nothing here knows a limit's name:
// the spec says each one's kind, range and help, and the server checks everything again.
//
//   spec param kind   on screen                         sent as
//   money             ₹ text, Indian grouping allowed    whole paise
//   lots, count       whole number                       number
//   percent, seconds  decimal                            number
//   flag              tick box                           true / false
//   choices           a tick box per choice              list
//   instruments       a list of contracts (+ search)     list

import type { AdminAccount, AdminUser, AlgoParamSpec, LimitValue, LimitsSpec } from "../../api/types";
import { price, rupees } from "../../lib/format";

/** What the form holds for one limit while it is being edited. */
export type LimitInput = string | boolean | string[];
export type LimitInputs = Record<string, LimitInput>;

const WHOLE = new Set(["lots", "count", "ticks", "minutes"]);
const DECIMAL = new Set(["percent", "seconds"]);
const LISTS = new Set(["choices", "instruments"]);

/** Paise as the form shows it: "50,00,000" for ₹50 lakh, "1,000.50" with paise. */
export function moneyText(paise: number): string {
  const text = price(paise); // "50,00,000.00"
  return text.endsWith(".00") ? text.slice(0, -3) : text;
}

/** "50,00,000", "₹ 1,000.5", "1000" → paise; null when it isn't an amount. Never floats. */
export function parseRupees(text: string): number | null {
  const clean = text.replace(/[₹,\s]/g, "");
  const m = /^(\d+)(?:\.(\d{1,2}))?$/.exec(clean);
  if (!m) return null;
  const paise = Number(m[1]) * 100 + Number((m[2] ?? "").padEnd(2, "0") || "0");
  return Number.isSafeInteger(paise) ? paise : null;
}

/** The form's starting values: the limits in force, written the way each kind is edited. */
export function limitInputs(spec: LimitsSpec, limits: Record<string, LimitValue>): LimitInputs {
  const out: LimitInputs = {};
  for (const p of spec.params) {
    const v = limits[p.name] ?? (p.default as LimitValue);
    if (p.kind === "flag") out[p.name] = v === true;
    else if (LISTS.has(p.kind)) out[p.name] = Array.isArray(v) ? [...v] : [];
    else if (p.kind === "money") out[p.name] = typeof v === "number" ? moneyText(v) : "";
    else out[p.name] = v === null || v === undefined ? "" : String(v);
  }
  return out;
}

/** One limit's value for the server, or the problem in words. */
export function parseLimit(p: AlgoParamSpec, raw: LimitInput | undefined): { ok: true; value: LimitValue } | { ok: false; problem: string } {
  if (p.kind === "flag") return { ok: true, value: raw === true };
  if (LISTS.has(p.kind)) {
    const list = Array.isArray(raw) ? [...new Set(raw)].sort() : [];
    if (p.kind === "choices") {
      const bad = list.filter((c) => !p.choices.includes(c));
      if (bad.length) return { ok: false, problem: `${p.label}: ${bad.join(", ")} isn't one of ${p.choices.join(", ")}` };
    }
    if (p.min !== null && list.length < p.min) return { ok: false, problem: `${p.label}: pick at least ${p.min}` };
    if (p.max !== null && list.length > p.max) return { ok: false, problem: `${p.label}: at most ${p.max}` };
    return { ok: true, value: list };
  }
  const text = typeof raw === "string" ? raw.trim() : "";
  if (!text) return { ok: false, problem: `${p.label} is required` };
  let value: number;
  if (p.kind === "money") {
    const paise = parseRupees(text);
    if (paise === null) return { ok: false, problem: `${p.label}: not an amount in rupees` };
    value = paise;
  } else if (WHOLE.has(p.kind)) {
    if (!/^\d+$/.test(text.replace(/,/g, ""))) return { ok: false, problem: `${p.label} must be a whole number` };
    value = Number(text.replace(/,/g, ""));
  } else if (DECIMAL.has(p.kind)) {
    value = Number(text);
    if (!Number.isFinite(value)) return { ok: false, problem: `${p.label} must be a number` };
  } else {
    return { ok: false, problem: `${p.label}: this screen can't edit a ${p.kind} yet` }; // the server's spec grew
  }
  const shown = (n: number) => (p.kind === "money" ? rupees(n) : String(n));
  if (p.min !== null && value < p.min) return { ok: false, problem: `${p.label} must be at least ${shown(p.min)}` };
  if (p.max !== null && value > p.max) return { ok: false, problem: `${p.label} must be at most ${shown(p.max)}` };
  return { ok: true, value };
}

/** The whole form for PUT …/limits, or every problem (each a sentence). */
export function parseLimits(spec: LimitsSpec, inputs: LimitInputs): { limits: Record<string, LimitValue> | null; problems: string[] } {
  const limits: Record<string, LimitValue> = {};
  const problems: string[] = [];
  for (const p of spec.params) {
    const r = parseLimit(p, inputs[p.name]);
    if (r.ok) limits[p.name] = r.value;
    else problems.push(r.problem);
  }
  return { limits: problems.length ? null : limits, problems };
}

/** The spec's help groups: the price bands sit together ("Price band: EQ", "Price band: FUT"). */
export function limitGroups(spec: LimitsSpec): { title: string; params: AlgoParamSpec[] }[] {
  const groups: { title: string; params: AlgoParamSpec[] }[] = [];
  for (const p of spec.params) {
    const title = p.label.includes(":") ? p.label.split(":")[0]!.trim() : "";
    const last = groups.at(-1);
    if (title && last?.title === title) last.params.push(p);
    else groups.push({ title, params: [p] });
  }
  return groups;
}

export type Tone = "good" | "bad" | "pending" | "dim";

/** How a user's row reads: off, locked, waiting for their own password, or fine. */
export function userState(u: AdminUser, now: number): { text: string; tone: Tone } {
  if (!u.active) return { text: "off", tone: "dim" };
  if (u.locked_until && Date.parse(u.locked_until) > now) return { text: "locked (failed logins)", tone: "bad" };
  if (u.must_change_password) return { text: "must set a password", tone: "pending" };
  return { text: u.sessions ? `on · ${u.sessions} signed in` : "on", tone: "good" };
}

/** How an account's row reads: off (closing only), not running (and why), or running. */
export function accountState(a: AdminAccount): { text: string; tone: Tone } {
  if (!a.active) return { text: "off: closing trades only", tone: "dim" };
  if (!a.running) return { text: `not running: ${a.not_running}`, tone: "pending" };
  return { text: "running", tone: "good" };
}

/** The authenticator link's parts: who it is for, and the key for typing in by hand. */
export function totpParts(uri: string): { account: string; issuer: string; secret: string } | null {
  try {
    const url = new URL(uri);
    if (url.protocol !== "otpauth:") return null;
    const secret = url.searchParams.get("secret") ?? "";
    const label = decodeURIComponent(url.pathname.replace(/^\/+/, ""));
    const issuer = url.searchParams.get("issuer") ?? (label.includes(":") ? label.split(":")[0]! : "");
    const account = label.includes(":") ? label.split(":").slice(1).join(":") : label;
    return secret ? { account, issuer, secret: secret.replace(/(.{4})/g, "$1 ").trim() } : null;
  } catch {
    return null;
  }
}
