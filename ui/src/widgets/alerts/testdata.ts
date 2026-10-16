// Alerts test data, shared by the model and widget tests.

import type { AlertSpec, MarketAlert } from "../../api/types";

export const FUT = "NFO:NIFTY26OCTFUT";

export const SPEC: AlertSpec = {
  kinds: [
    { code: "PRICE_ABOVE", name: "Price crosses above", help: "When the last price rises to the level", family: "price", params: [{ name: "level_paise", label: "Price", kind: "price", help: "", required: true, default: null, min: 1, max: null, choices: [] }] },
    {
      code: "PCT_MOVE",
      name: "Moves from the previous close",
      help: "When the move reaches the percent",
      family: "price",
      params: [
        { name: "pct", label: "Move", kind: "percent", help: "", required: true, default: null, min: 0.01, max: 100, choices: [] },
        { name: "direction", label: "Direction", kind: "choice", help: "", required: true, default: "either", min: null, max: null, choices: ["up", "down", "either"] },
      ],
    },
    {
      code: "SPREAD_ABOVE",
      name: "Spread crosses above",
      help: "A − B rises to the level",
      family: "spread",
      params: [
        { name: "other", label: "Leg B", kind: "instrument", help: "", required: true, default: null, min: null, max: null, choices: [] },
        { name: "level_paise", label: "Spread (A − B)", kind: "price_offset", help: "", required: true, default: null, min: null, max: null, choices: [] },
      ],
    },
  ],
  repeat: ["once", "every"],
  cooldown_s: { min: 10, max: 86400, default: 300 },
  max_alerts: 200,
  note_max: 200,
};

export function alert(id: string, extra: Partial<MarketAlert> = {}): MarketAlert {
  return {
    alert_id: id,
    user_id: "ayush",
    kind: "PRICE_ABOVE",
    instrument_id: FUT,
    params: { level_paise: 2510000 },
    repeat: "once",
    cooldown_s: 300,
    note: "",
    webhook: false,
    enabled: true,
    created_at: `2026-10-16T04:00:0${id.slice(-1)}+00:00`,
    armed: true,
    done: false,
    active: true,
    fired_count: 0,
    last_fired_at: null,
    last_text: "",
    status: "watching",
    webhook_error: "",
    webhook_ok_at: null,
    ...extra,
  };
}
