// Research test data, shared by the model and widget tests.

import type { BacktestForm, BacktestRun, BacktestSummary } from "../../api/types";

export const FUT = "NFO:NIFTY26OCTFUT";

export const FORM: BacktestForm = {
  algos: [
    {
      code: "SLICER",
      name: "Slicer",
      help: "Equal slices on a clock",
      params: [
        { name: "slices", label: "Slices", kind: "count", help: "", required: true, default: 10, min: 1, max: 100, choices: [] },
        { name: "mode", label: "Price", kind: "choice", help: "", required: true, default: "join", min: null, max: null, choices: ["join", "cross"] },
      ],
    },
    { code: "TWAP", name: "TWAP", help: "Even over time", params: [{ name: "minutes", label: "Minutes", kind: "minutes", help: "", required: true, default: 30, min: 1, max: 375, choices: [] }] },
  ],
  days: ["2026-10-16", "2026-10-15", "2026-10-14"],
  products: ["CNC", "MIS", "NRML"],
  candles: true,
  defaults: { product: "NRML", start: "09:15", capital_paise: 100000000 },
  max_days: 2,
};

export function summary(extra: Partial<BacktestSummary> = {}): BacktestSummary {
  return {
    days_run: 2,
    days_skipped: [],
    gross_paise: 50000,
    charges_paise: 10000,
    net_paise: 40000,
    max_drawdown_paise: 5000,
    winning_days: 1,
    win_rate: 0.5,
    trades: 4,
    equity_curve: [
      ["2026-10-14", -5000],
      ["2026-10-15", 40000],
    ],
    ...extra,
  };
}

export function run(id: string, extra: Partial<BacktestRun> = {}): BacktestRun {
  return {
    run_id: id,
    spec: { days: ["2026-10-14", "2026-10-15"], algo: "SLICER", instrument_id: FUT, side: "BUY", qty: 150, params: { slices: 2 }, product: "NRML", start: "09:15", capital_paise: 100000000, name: "" },
    candle_based: false,
    state: "done",
    progress: 2,
    days: 2,
    error: "",
    created_at: "2026-10-16T06:00:00+00:00",
    summary: summary(),
    ...extra,
  };
}
