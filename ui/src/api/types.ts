// The shapes the server sends (docs/design/stream.md and the REST API). Money in paise.

export type Side = "BUY" | "SELL";
export type OrderType = "MARKET" | "LIMIT" | "SL" | "SL-M";
export type Product = "CNC" | "MIS" | "NRML";
export type Validity = "DAY" | "IOC";
export type OrderStatus = "PENDING" | "OPEN" | "PARTIAL" | "FILLED" | "CANCELLED" | "REJECTED";
export type Pending = "NONE" | "UNCONFIRMED" | "CANCEL";

export interface OrderRequest {
  client_order_id: string;
  account_id: string;
  user_id: string;
  instrument_id: string;
  side: Side;
  qty: number;
  order_type: OrderType;
  product: Product;
  validity: Validity;
  price_paise: number | null;
  trigger_paise: number | null;
  market_protection: string | null;
  algo_id: string | null;
  parent_id: string | null;
}

export interface OrderView {
  request: OrderRequest;
  broker_order_id: string | null;
  status: OrderStatus;
  filled_qty: number;
  avg_price_paise: number;
  reason: string;
  pending: Pending;
  created_at: string;
  updated_at: string;
}

export interface Fill {
  client_order_id: string;
  qty: number;
  price_paise: number;
  exact: boolean;
}

export interface Tick {
  instrument_id: string;
  ltp_paise: number;
  last_qty: number | null;
  volume: number | null;
  avg_price_paise: number | null;
  buy_qty: number | null;
  sell_qty: number | null;
  open_paise: number | null;
  high_paise: number | null;
  low_paise: number | null;
  close_paise: number | null;
  open_interest: number | null;
  exchange_time: string | null;
  bids: [number, number, number][]; // [price, qty, orders], best first
  asks: [number, number, number][];
}

export interface Position {
  instrument_id: string;
  product: Product;
  qty: number;
  avg_price_paise: string; // exact decimal text, e.g. "10007.5"
  ltp_paise: number | null;
  realised_paise: number;
  unrealised_paise: number;
  pnl_paise: number;
  buy_qty: number;
  sell_qty: number;
  buy_value_paise: number;
  sell_value_paise: number;
  updated_at: string | null;
}

export interface Instrument {
  id: string;
  exchange: string;
  symbol: string;
  kind: "EQ" | "FUT" | "CE" | "PE" | "INDEX";
  tick_paise: number;
  lot_size: number;
  expiry: string | null;
  strike_paise: number | null;
  underlying: string | null;
  tradable: boolean;
  ltp_paise?: number | null;
}

export interface AccountSummary {
  id: string;
  broker: string;
  broker_client_id: string;
  can_trade: boolean;
  running: boolean;
  stopped: boolean;
  stop_reason: string;
}

export interface Me {
  user: { id: string; name: string; roles: string[] };
  accounts: AccountSummary[];
  firm_stopped: boolean;
}

export interface PositionsSnapshot {
  positions: Position[];
  realised_paise: number;
  unrealised_paise: number;
  day_pnl_paise: number;
  reconcile: {
    checked_at: string | null;
    ok: boolean | null;
    error: string;
    alerts: { instrument_id: string; ours: number; broker: number; since: string; message: string }[];
  };
}

export interface AuditEvent {
  seq: number; // position in the event log: later events have bigger numbers
  ts: string;
  account_id: string; // the account, "account <id>" (its kill switch) or "firm"
  kind: string; // an order event kind, KILL_SWITCH or POSITION_MISMATCH_…
  client_order_id: string;
  detail: string;
  order: OrderView | null;
  fill: Fill | null;
  extra: Record<string, unknown>; // e.g. {engaged, by} for kill switches
}

// --- stream messages (server -> browser) ---------------------------------------------------

export type ServerMessage =
  | { type: "hello"; user: string; accounts: string[]; flush_ms: number; max_instruments: number }
  | { type: "ticks"; ticks: Tick[] }
  | {
      type: "order";
      account_id: string;
      kind: string;
      detail: string;
      order: OrderView | null;
      fill: Fill | null;
    }
  | { type: "positions"; account_id: string; positions: Position[]; day_pnl_paise: number }
  | { type: "kill"; scope: string; engaged: boolean; reason: string; by: string; at: string | null }
  | {
      type: "position_alert";
      account_id: string;
      state: "raised" | "cleared";
      instrument_id: string;
      ours: number;
      broker: number;
      message: string;
    }
  | { type: "error"; message: string }
  | { type: "pong" };
