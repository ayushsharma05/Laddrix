// Keyboard shortcuts (K13.4): one list of every action a key can start, each with its default
// key, and the user's own choices on top (saved on the server, so they follow the user). Widgets
// ask "which action is this key?" for their own scope instead of hard-coding keys.
//
//   key press ─► chord ("Ctrl+Shift+K") ─► the user's bindings, else the defaults ─► action id
//
// Scopes: "global" keys work anywhere; a widget's keys (ticket, ladder, chart) only while it has
// the focus. A global key can't also be a widget key (both would fire), and two actions of one
// scope can't share a key. Keys the browser keeps for itself (closing the tab, …) are refused.
// STOP keeps a fixed fallback key that can never be removed: it must always be reachable.

export type Scope = "global" | "ticket" | "ladder" | "chart";

export interface KeyAction {
  id: string;
  label: string;
  scope: Scope;
  keys: string[]; // the defaults
  fixed?: string; // a key this action always has, whatever the user sets (STOP)
  help?: string;
}

export const SCOPES: { scope: Scope; label: string }[] = [
  { scope: "global", label: "Anywhere" },
  { scope: "ticket", label: "Order ticket (when it has the focus)" },
  { scope: "ladder", label: "Ladder (when it has the focus)" },
  { scope: "chart", label: "Chart (when it has the focus)" },
];

export const ACTIONS: KeyAction[] = [
  { id: "stop", label: "STOP trading (opens the stop dialog)", scope: "global", keys: ["Ctrl+Shift+K"], fixed: "Ctrl+Shift+K", help: "Ctrl+Shift+K always works too" },
  { id: "search", label: "Search a product", scope: "global", keys: ["/"] },
  { id: "save-layout", label: "Save the layout", scope: "global", keys: ["Ctrl+S"] },
  { id: "open-ticket", label: "Open an order ticket", scope: "global", keys: ["Alt+T"] },
  { id: "open-ladder", label: "Open a ladder", scope: "global", keys: ["Alt+L"] },
  { id: "open-chart", label: "Open a chart", scope: "global", keys: ["Alt+C"] },
  { id: "ticket-send", label: "Send the order (its confirm rules still apply)", scope: "ticket", keys: ["Enter"] },
  { id: "ticket-clear", label: "Clear the ticket", scope: "ticket", keys: ["Escape"] },
  { id: "ticket-buy", label: "Buy side", scope: "ticket", keys: ["Alt+B"] },
  { id: "ticket-sell", label: "Sell side", scope: "ticket", keys: ["Alt+S"] },
  { id: "ticket-up", label: "Price one tick up", scope: "ticket", keys: ["ArrowUp"] },
  { id: "ticket-down", label: "Price one tick down", scope: "ticket", keys: ["ArrowDown"] },
  { id: "ladder-centre", label: "Centre on the last price", scope: "ladder", keys: ["Space"] },
  { id: "ladder-clear", label: "Clear the selection and lots", scope: "ladder", keys: ["Escape"] },
  { id: "chart-back", label: "Pan back in time", scope: "chart", keys: ["ArrowLeft"] },
  { id: "chart-forward", label: "Pan forward", scope: "chart", keys: ["ArrowRight"] },
  { id: "chart-zoom-in", label: "Zoom in", scope: "chart", keys: ["Plus"] },
  { id: "chart-zoom-out", label: "Zoom out", scope: "chart", keys: ["Minus"] },
  { id: "chart-reset", label: "Back to the latest candles", scope: "chart", keys: ["Home"] },
];

/** The user's choices: an action's keys replace its defaults (an empty list: no key). */
export type Bindings = Record<string, string[]>;

/** Keys the browser or the system keeps (refused: pressing them would close or reload). */
export const RESERVED = new Set([
  "Ctrl+W", "Ctrl+Shift+W", "Ctrl+T", "Ctrl+Shift+T", "Ctrl+N", "Ctrl+Shift+N", "Ctrl+Tab",
  "Ctrl+Shift+Tab", "Ctrl+R", "Ctrl+Shift+R", "F5", "Ctrl+F5", "Ctrl+L", "Ctrl+Q", "Alt+F4",
  "Ctrl+PageUp", "Ctrl+PageDown", "F11", "F12", "Ctrl+Shift+I", "Ctrl+Shift+J", "Ctrl+P",
  "Ctrl+F", "Ctrl+H", "Ctrl+J", "Ctrl+D", "Ctrl+O", "Ctrl+U", "Ctrl+Plus", "Ctrl+Minus", "Ctrl+0",
  "Tab", "Shift+Tab", // moving between fields must keep working
]); // prettier-ignore

const NAMES: Record<string, string> = { " ": "Space", Esc: "Escape", "+": "Plus", "-": "Minus", "=": "Plus" };
const MODIFIERS = new Set(["Control", "Shift", "Alt", "Meta", "AltGraph", "CapsLock", "Fn"]);

/** A key press as one name: "Ctrl+Shift+K", "Alt+B", "Enter", "/", "ArrowUp". Null for a
 *  modifier pressed on its own. Letters are upper case; Shift is named only where it matters
 *  (with letters, other modifiers or named keys; not for "/" or "?" typed with Shift). */
export function chordOf(e: Pick<KeyboardEvent, "key" | "code" | "ctrlKey" | "metaKey" | "altKey" | "shiftKey">): string | null {
  if (MODIFIERS.has(e.key)) return null;
  // with Alt (macOS turns Alt+B into "∫") or Ctrl, read the key from its place on the keyboard
  let key = e.key;
  if ((e.altKey || e.ctrlKey) && /^Key[A-Z]$/.test(e.code)) key = e.code.slice(3);
  else if ((e.altKey || e.ctrlKey) && /^Digit\d$/.test(e.code)) key = e.code.slice(5);
  const printable = key.length === 1 && !/[a-zA-Z0-9 ]/.test(key); // "/", "?", "+": the character says it
  key = NAMES[key] ?? (key.length === 1 ? key.toUpperCase() : key);
  const parts: string[] = [];
  if (e.ctrlKey || e.metaKey) parts.push("Ctrl");
  if (e.altKey) parts.push("Alt");
  if (e.shiftKey && !(printable && parts.length === 0)) parts.push("Shift");
  parts.push(key);
  return parts.join("+");
}

/** Each action's keys now: the user's choice (or the default), plus its fixed key. */
export function keysOf(action: KeyAction, bindings: Bindings): string[] {
  const chosen = bindings[action.id] ?? action.keys;
  return action.fixed && !chosen.includes(action.fixed) ? [...chosen, action.fixed] : chosen;
}

/** Which action this chord starts in this scope (a global one wins anywhere). */
export function actionFor(chord: string | null, scope: Scope, bindings: Bindings): string | null {
  if (!chord) return null;
  for (const want of scope === "global" ? ["global"] : [scope, "global"]) {
    const hit = ACTIONS.find((a) => a.scope === want && keysOf(a, bindings).includes(chord));
    if (hit) return hit.id;
  }
  return null;
}

/** Why `chord` can't be given to `actionId`, in words; null when it can. */
export function refusal(actionId: string, chord: string, bindings: Bindings): string | null {
  const action = ACTIONS.find((a) => a.id === actionId);
  if (!action) return "no such action";
  if (RESERVED.has(chord)) return `${chord} belongs to the browser (closing, reloading or moving between tabs and fields)`;
  const clash = ACTIONS.find(
    (a) => a.id !== actionId && keysOf(a, bindings).includes(chord) && (a.scope === action.scope || a.scope === "global" || action.scope === "global"),
  );
  if (clash) return `${chord} is already “${clash.label}”${clash.scope === action.scope ? "" : ` (${SCOPES.find((s) => s.scope === clash.scope)!.label.toLowerCase()})`}`;
  return null;
}

/** The bindings after giving `chord` to an action (added to its keys, never duplicated). */
export function withKey(bindings: Bindings, actionId: string, chord: string): Bindings {
  const action = ACTIONS.find((a) => a.id === actionId)!;
  const now = bindings[actionId] ?? action.keys;
  return { ...bindings, [actionId]: now.includes(chord) ? now : [...now, chord] };
}

/** The bindings after taking `chord` off an action (its fixed key can't be taken off). */
export function withoutKey(bindings: Bindings, actionId: string, chord: string): Bindings {
  const action = ACTIONS.find((a) => a.id === actionId)!;
  if (action.fixed === chord) return bindings;
  const now = bindings[actionId] ?? action.keys;
  return { ...bindings, [actionId]: now.filter((k) => k !== chord) };
}

/** Saved bindings from the server, kept only where they are still valid (an action that no
 *  longer exists, a reserved key or a clash falls back to the defaults). */
export function cleanBindings(raw: unknown): Bindings {
  if (!raw || typeof raw !== "object") return {};
  const out: Bindings = {};
  for (const [id, keys] of Object.entries(raw as Record<string, unknown>)) {
    if (!ACTIONS.some((a) => a.id === id) || !Array.isArray(keys)) continue;
    const list = keys.filter((k): k is string => typeof k === "string" && !RESERVED.has(k));
    if (keys.length && !list.length) continue; // nothing usable left: the defaults, never a dead key
    out[id] = [...new Set(list)];
  }
  for (const id of Object.keys(out)) {
    const action = ACTIONS.find((a) => a.id === id)!;
    const without = { ...out };
    delete without[id];
    if (out[id]!.some((k) => k !== action.fixed && refusal(id, k, without) !== null)) delete out[id]; // a clash: defaults
  }
  return out;
}
