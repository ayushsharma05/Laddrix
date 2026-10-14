// Shared test data.
import type { Me, OrderView } from "../api/types";

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

