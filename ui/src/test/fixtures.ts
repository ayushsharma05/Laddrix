// Shared test data.
import type { Me, Need, OrderSpec, OrderTypeSpec, OrderView } from "../api/types";

export const ME: Me = {
  user: { id: "ayush", name: "Ayush", roles: ["trader"] },
  accounts: [
    { id: "acc-view", broker: "kite", broker_client_id: "AB1", can_trade: false, running: true, stopped: false, stop_reason: "" },
    { id: "acc-a", broker: "paper", broker_client_id: "", can_trade: true, running: true, stopped: false, stop_reason: "" },
  ],
  firm_stopped: false,
};

export function order(id: string, status: OrderView["status"], updated: string, extra: Partial<OrderView> = {}): OrderView {
  return {
    request: {
      client_order_id: id,
      account_id: "acc-a",
      user_id: "ayush",
      instrument_id: "NFO:NIFTY26OCTFUT",
      side: "BUY",
      qty: 75,
      order_type: "LIMIT",
      product: "NRML",
      validity: "DAY",
      price_paise: 2501000,
      trigger_paise: null,
      market_protection: null,
      algo_id: null,
      parent_id: null,
    },
    broker_order_id: null,
    status,
    filled_qty: 0,
    avg_price_paise: 0,
    reason: "",
    pending: "NONE",
    created_at: "2026-10-14T04:40:00+00:00",
    updated_at: updated,
    ...extra,
  };
}


/** The server's standard NSE/BSE order spec (domain/order_spec.py), as the API sends it. */
export function orderSpec(instrumentId = "NFO:NIFTY26OCTFUT", extra: Partial<OrderSpec> = {}): OrderSpec {
  const t = (code: string, label: string, name: string, price: Need, trigger: Need, mp: Need, validities: string[], rules: string[], click: boolean): OrderTypeSpec => ({
    code, label, name, help: `${name} help`, price, trigger, market_protection: mp, validities, rules, priced_by_click: click,
  });
  return {
    instrument_id: instrumentId,
    tradable: true,
    tick_paise: 10,
    lot_size: 75,
    order_types: [
      t("LIMIT", "LMT", "Limit", "required", "none", "none", ["DAY", "IOC"], [], true),
      t("MARKET", "MKT", "Market", "none", "none", "auto", ["DAY", "IOC"], [], true),
      t("SL", "SL", "Stop-loss limit", "required", "required", "none", ["DAY"], ["stop_trigger", "stop_limit"], false),
      t("SL-M", "SL-M", "Stop-loss market", "none", "required", "auto", ["DAY"], ["stop_trigger"], false),
    ],
    products: ["NRML", "MIS"],
    auto_protection_pct: "2",
    max_modifications: 25,
    ...extra,
  };
}
