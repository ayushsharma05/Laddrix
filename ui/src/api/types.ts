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
  /** The broker's own daily login: "live", "login_needed", or null when it has none. */
  broker_session?: "live" | "login_needed" | null;
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
  | { type: "algo"; account_id: string; kind: string; detail: string; algo: AlgoView }
  | { type: "ops"; key: string; level: "INFO" | "WARN" | "ERROR" | "OK"; text: string; active: boolean; at: string }
  | ({ type: "chain" } & OptionChain)
  | { type: "error"; message: string }
  | { type: "pong" };

// --- what can be ordered (GET /api/accounts/{a}/instruments/{id}/order-spec) -----------------
// The server describes order types and their fields; screens draw from this and hard-code none.

export type Need = "required" | "none" | "auto";

export interface OrderTypeSpec {
  code: string; // sent as order_type, e.g. "LIMIT", "SL-M"
  label: string; // short, for buttons: "LMT"
  name: string; // in words: "Stop-loss market"
  help: string;
  price: Need;
  trigger: Need;
  market_protection: Need;
  validities: string[];
  rules: string[]; // e.g. "stop_trigger", "stop_limit"
  priced_by_click: boolean; // a ladder click can place it
}

export interface OrderSpec {
  instrument_id: string;
  tradable: boolean;
  tick_paise: number;
  lot_size: number;
  order_types: OrderTypeSpec[];
  products: string[];
  auto_protection_pct: string; // decimal text, e.g. "2"
  max_modifications: number;
}

// --- algos (GET …/algo-specs, …/algos; stream "algo") -------------------------------------------
// Every algo describes its settings; the start form is drawn from this, nothing hard-coded.

export type AlgoParamKind =
  | "lots"
  | "price"
  | "price_offset"
  | "ticks"
  | "seconds"
  | "count"
  | "percent"
  | "choice"
  | "flag"
  | "instrument";

export interface AlgoParamSpec {
  name: string;
  label: string;
  kind: AlgoParamKind | string; // an unknown kind is shown as a plain text box
  help: string;
  required: boolean;
  default: unknown;
  min: number | null;
  max: number | null;
  choices: string[];
}

export interface AlgoSpec {
  code: string;
  name: string;
  help: string;
  params: AlgoParamSpec[];
}

export type AlgoState = "WORKING" | "PAUSED" | "STOPPING" | "DONE" | "STOPPED" | "FAILED";

export interface AlgoView {
  algo_id: string;
  code: string;
  account_id: string;
  user_id: string;
  instrument_id: string;
  side: Side;
  qty: number;
  params: Record<string, unknown>;
  state: AlgoState;
  filled_qty: number;
  avg_price_paise: number;
  working_qty: number;
  children: string[];
  reason: string;
  status: string;
  extra: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

// --- options (GET /api/options/…, stream "chain") ---------------------------------------------

export interface ChainLeg {
  instrument_id: string;
  ltp_paise: number | null;
  bid_paise: number | null;
  ask_paise: number | null;
  volume: number | null;
  oi: number | null;
  oi_change: number | null; // since the first OI seen today
  iv: number | null; // a fraction: 0.12 = 12%
  delta: number | null;
  gamma: number | null;
  theta: number | null; // rupees per calendar day
  vega: number | null; // rupees per IV point
}

export interface ChainRow {
  strike_paise: number;
  call: ChainLeg | null;
  put: ChainLeg | null;
}

export interface OptionChain {
  underlying: string;
  expiry: string; // YYYY-MM-DD
  forward_paise: number | null;
  atm_strike_paise: number | null;
  days_to_expiry: number;
  rate: number;
  pcr: number | null;
  max_pain_paise: number | null;
  atm_iv: number | null;
  rows: ChainRow[];
}

export interface StrategyLegIn {
  instrument_id: string;
  side: Side;
  lots: number;
  price_paise?: number; // entry price; default: the live mid
}

export interface StrategyTemplate {
  code: string;
  name: string;
  view: string;
  legs: { side: Side; kind: string; offset: number; ratio: number }[];
  params: { name: string; label: string; kind: string; min: number; max: number; default: number }[];
}

export interface StrategyAnalysis {
  underlying: string;
  expiry: string;
  forward_paise: number;
  prices: number[]; // rupees
  payoff_expiry: number[]; // rupees, whole position
  payoff_today: number[];
  breakevens: number[];
  max_profit: number | "unlimited";
  max_loss: number | "unlimited";
  net_premium: number; // + = credit
  net_greeks: { delta: number; gamma: number; theta: number; vega: number };
  legs: {
    instrument_id: string;
    side: Side;
    lots: number;
    lot_size: number;
    strike_paise: number;
    kind: string;
    expiry: string;
    price_paise: number;
    iv: number | null;
  }[];
}
