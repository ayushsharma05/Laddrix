// The limits spec and limits as the server sends them (a part of the real spec, from
// laddrix.risk.limits_spec, so the screen is tested against the real shapes).
import type { LimitValue, LimitsSpec } from "../../api/types";

export const SPEC: LimitsSpec = {
  "code": "RISK_LIMITS",
  "name": "Risk limits",
  "help": "What the pre-trade checks allow on this account; an order breaking one is refused with the reason.",
  "family": "limits",
  "params": [
    {
      "name": "allowed_exchanges",
      "label": "Exchanges",
      "kind": "choices",
      "help": "Where orders may go",
      "required": true,
      "default": [
        "BFO",
        "BSE",
        "NFO",
        "NSE"
      ],
      "min": 1,
      "max": null,
      "choices": [
        "BFO",
        "BSE",
        "NFO",
        "NSE"
      ]
    },
    {
      "name": "blocked_instruments",
      "label": "Blocked contracts",
      "kind": "instruments",
      "help": "Contracts no order may use, e.g. a stock under a ban",
      "required": true,
      "default": [],
      "min": 0,
      "max": 200,
      "choices": []
    },
    {
      "name": "allow_option_selling",
      "label": "Allow option selling",
      "kind": "flag",
      "help": "Selling options to open can lose far more than the premium",
      "required": true,
      "default": false,
      "min": null,
      "max": null,
      "choices": []
    },
    {
      "name": "max_order_lots",
      "label": "Most lots per order",
      "kind": "lots",
      "help": "Bigger orders are refused (closing orders are sent in pieces of this size)",
      "required": true,
      "default": 10,
      "min": 1,
      "max": 10000,
      "choices": []
    },
    {
      "name": "max_order_value_paise",
      "label": "Most value per order",
      "kind": "money",
      "help": "Quantity × the worst price it could fill at",
      "required": true,
      "default": 500000000,
      "min": 100000,
      "max": 100000000000,
      "choices": []
    },
    {
      "name": "price_band_pct.EQ",
      "label": "Price band: EQ",
      "kind": "percent",
      "help": "How far a EQ order's price may be from the last price",
      "required": true,
      "default": 5.0,
      "min": 0.5,
      "max": 100,
      "choices": []
    },
    {
      "name": "price_band_pct.FUT",
      "label": "Price band: FUT",
      "kind": "percent",
      "help": "How far a FUT order's price may be from the last price",
      "required": true,
      "default": 5.0,
      "min": 0.5,
      "max": 100,
      "choices": []
    },
    {
      "name": "duplicate_window_s",
      "label": "Double-click window",
      "kind": "seconds",
      "help": "The same order again this soon is taken as a double click (0: off)",
      "required": true,
      "default": 1.0,
      "min": 0,
      "max": 10,
      "choices": []
    }
  ]
};

export const LIMITS: Record<string, LimitValue> = {
  "allowed_exchanges": [
    "NFO",
    "NSE"
  ],
  "blocked_instruments": [],
  "allow_option_selling": false,
  "max_order_lots": 10,
  "max_order_value_paise": 500000000,
  "duplicate_window_s": 1.0,
  "price_band_pct.EQ": 5.0,
  "price_band_pct.FUT": 5.0
};
