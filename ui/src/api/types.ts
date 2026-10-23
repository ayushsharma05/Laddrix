// The shapes the server sends (docs/design/stream.md and the REST API). Money in paise.

export type Side = "BUY" | "SELL";
export type OrderType = "MARKET" | "LIMIT" | "SL" | "SL-M";
export type Product = "CNC" | "MIS" | "NRML";
export type Validity = "DAY" | "IOC";
export type OrderStatus = "PENDING" | "OPEN" | "PARTIAL" | "FILLED" | "CANCELLED" | "REJECTED";
export type Pending = "NONE" | "UNCONFIRMED" | "CANCEL" | "MODIFY"; // MODIFY: a change on its way

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
  basket_id?: string | null;
  reduce_only?: boolean; // a close: never more than the open position (#261)
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

/** A future's or option's expiry, as the server judges it now (#290; refdata/expiry.py). */
export interface ExpiryInfo {
  expires_at: string; // 15:30 India time on the expiry date
  settlement: "cash" | "physical";
  expires_today: boolean;
  expired: boolean;
  opening_cutoff: string | null; // from here, only closing trades
  opening_closed: boolean;
  note: string; // one sentence to show as it is
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
  expiry_info?: ExpiryInfo | null; // null for shares and indices
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
  /** false: an admin turned the account off (only closing trades, cancels always work). */
  active?: boolean;
}

export interface Me {
  /** must_change_password: an admin set a one-time password; nothing else until it's changed. */
  user: { id: string; name: string; roles: string[]; must_change_password?: boolean };
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
    // product: the one that differs; null: the net across products differs (the broker didn't say)
    alerts: { instrument_id: string; product: string | null; ours: number; broker: number; since: string; message: string }[];
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
      product: string | null;
      ours: number;
      broker: number;
      message: string;
    }
  | { type: "algo"; account_id: string; kind: string; detail: string; algo: AlgoView }
  | { type: "gtt"; account_id: string; gtt: GttView }
  | ({ type: "portfolio" } & PortfolioSnapshot)
  | ({ type: "charges"; account_id: string } & ChargeBill)
  | { type: "mtm"; account_id: string; kind: "position" | "portfolio"; key: string; text: string; closing: boolean }
  | { type: "ops"; key: string; level: "INFO" | "WARN" | "ERROR" | "OK"; text: string; active: boolean; at: string }
  | ({ type: "chain" } & OptionChain)
  | { type: "candle"; instrument_id: string; interval: string; candle: Candle | null }
  | { type: "market_alert"; alert: MarketAlert; text: string; at: string }
  | { type: "market_alert_state"; alert: MarketAlert }
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
  expiry?: ExpiryInfo | null; // F&O only: expires today? cut-off? (#290)
}

// --- algos (GET …/algo-specs, …/algos; stream "algo") -------------------------------------------
// Every algo describes its settings; the start form is drawn from this, nothing hard-coded.

export type AlgoParamKind =
  | "lots"
  | "price"
  | "price_offset"
  | "ticks"
  | "seconds"
  | "minutes"
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
  family?: string; // how screens group algos: "execution", "protection"
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
  expiry_info?: ExpiryInfo | null; // expires today? how it settles (#290)
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

// --- baskets (POST /api/accounts/{a}/baskets/check | send) -------------------------------------

export interface BasketLegIn {
  instrument_id: string;
  side: Side;
  lots: number;
  product?: string;
  price_paise?: number;
  at?: "touch" | "mid";
}

export interface BasketCheck {
  ok: boolean;
  legs: { instrument_id: string; side: Side; qty: number; price_paise: number; problem: string | null }[];
  margin: { initial_paise: number; final_paise: number; hedge_benefit_paise: number } | null;
  margin_note: string;
}

export interface BasketSent {
  basket_id: string;
  sent: OrderView[];
  not_sent: { instrument_id: string; side: Side; qty: number; price_paise: number }[];
  stopped: string;
}

// --- GTT: orders held by the broker (GET …/gtt-spec, …/gtt; stream "gtt") ------------------------

export interface GttLeg {
  trigger_paise: number;
  side: Side;
  qty: number;
  price_paise: number;
}

export type GttStatus = "active" | "triggered" | "disabled" | "expired" | "cancelled" | "rejected" | "deleted";

export interface GttView {
  gtt_id: string;
  account_id: string;
  instrument_id: string;
  product: string;
  kind: string; // "single" | "two-leg" (from the spec)
  legs: GttLeg[];
  status: GttStatus | string;
  order_id: string | null;
  fired_leg: number | null;
  reason: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface GttSpec {
  instrument_id: string;
  kinds: string[];
  legs: Record<string, number>; // how many legs each kind has
  labels: Record<string, string>;
  tick_paise: number;
  lot_size: number;
  products: string[];
  min_distance_pct: number;
  expiry_days: number;
  max_active: number;
  help: Record<string, string>;
}

// --- funds, holdings, tradebook from the broker (GET …/funds|holdings|trades; stream "portfolio") ---
// Every figure comes from the server; a failed refresh keeps the last figures with `error` set.

export type PortfolioKind = "funds" | "holdings" | "trades";

export interface FundsLine {
  label: string;
  paise: number;
}

export interface Funds {
  segment: string;
  available_paise: number;
  used_paise: number;
  opening_paise: number;
  collateral_paise: number;
  available_lines: FundsLine[];
  used_lines: FundsLine[];
  estimate: boolean; // worked out by MockTrade (paper), not the broker's own figures
}

export interface Holding {
  instrument_id: string;
  qty: number;
  t1_qty: number;
  used_qty: number;
  total_qty: number;
  avg_price_paise: number;
  ltp_paise: number | null;
  close_paise: number | null;
  invested_paise: number;
  value_paise: number | null;
  pnl_paise: number | null;
  day_change_paise: number | null;
}

export interface Trade {
  trade_id: string;
  broker_order_id: string;
  client_order_id: string; // "" for orders placed outside MockTrade
  instrument_id: string;
  side: Side;
  product: string;
  qty: number;
  price_paise: number;
  value_paise: number;
  ts: string | null;
}

export interface PortfolioSnapshot {
  account_id: string;
  kind: PortfolioKind;
  items: Funds[] | Holding[] | Trade[];
  as_of: string | null; // when the broker last answered
  error: string; // why the latest refresh failed ("" when it worked)
}

// --- charges: the day's, and an order's estimate (GET …/charges, POST …/charges/estimate) ------

export interface ChargeBill {
  total_paise: number;
  turnover_paise: number;
  lines: { label: string; paise: number }[]; // brokerage, STT, exchange, SEBI, IPFT, stamp, GST, DP
  by_position: Record<string, number>; // "instrument_id|product" -> its charges today (positionKey)
  schedule: { name: string; checked_on: string; source: string };
}

// --- end-of-day reports and P&L history (GET …/reports, …/reports/{day}, …/pnl) ---------------

export interface ReportDays {
  today: string; // YYYY-MM-DD, India
  kept: string[]; // days whose report was kept after the close
}

export interface DayReport {
  account_id: string;
  day: string;
  generated_at: string;
  final: boolean; // kept after the close (otherwise a preview or a rebuild)
  priced: boolean;
  notes: string[];
  schedule: string;
  totals: { realised_paise: number; unrealised_paise: number; gross_paise: number; charges_paise: number; net_paise: number; turnover_paise: number; trades: number };
  charge_lines: { label: string; paise: number }[];
  positions: {
    instrument_id: string;
    product: string;
    carried_qty: number;
    reference_paise: number | null;
    buy_qty: number;
    buy_value_paise: number;
    sell_qty: number;
    sell_value_paise: number;
    closing_qty: number;
    mark_paise: number | null;
    realised_paise: number;
    unrealised_paise: number;
    gross_paise: number;
    charges_paise: number;
    net_paise: number;
  }[];
  strategies: { strategy: string; trades: number; gross_paise: number; charges_paise: number; net_paise: number }[];
  trades: { ts: string; client_order_id: string; instrument_id: string; side: Side; product: string; qty: number; price_paise: number; value_paise: number; strategy: string }[];
  rejects: { ts: string; detail: string }[];
  alerts: { ts: string; kind: string; detail: string }[];
}

export interface PnlHistory {
  account_id: string;
  by: "day" | "instrument" | "strategy";
  from: string;
  to: string;
  rows: { key: string; gross_paise: number; charges_paise: number; net_paise: number; trades: number }[];
  total: { gross_paise: number; charges_paise: number; net_paise: number };
  missing_days: string[]; // trading days without a kept report (not counted as zero)
}

// --- margin: what an order would block (POST /api/accounts/{a}/margin) ------------------------

export interface MarginCheck {
  exit: boolean; // only reduces the position: never checked, always allowed
  required_paise: number | null; // null: the broker couldn't say
  available_paise: number | null; // null: funds unknown
  enough: boolean | null; // null: can't be told (the order still goes, with a warning)
  note: string;
  checked_before_sending: boolean; // the account refuses orders without enough margin
}

// --- MTM limits and the square-off assistant (…/mtm, …/square-off) ----------------------------

export interface MtmPositionRule {
  key: string; // "instrument_id|product"
  instrument_id: string;
  product: string;
  max_loss_paise: number | null;
  max_loss_pct: number | null;
  auto_close: boolean;
  fired_today: boolean;
}

export interface MtmBreach {
  id: string;
  key: string; // the rule's key, or "portfolio"
  kind: "position" | "portfolio";
  text: string;
  state: "ALERTED" | "REQUESTED" | "WORKING" | "BLOCKED" | "UNKNOWN" | "COMPLETED";
  detail: string; // the server's words: what is left, or why
  attempts: number;
  at: string;
}

export interface MtmRules {
  positions: MtmPositionRule[];
  portfolio: { max_loss_paise: number | null; target_paise: number | null; auto_close: boolean; fired_today: boolean } | null;
  breaches?: MtmBreach[]; // what each limit reached did (#263)
  problem?: string; // saved limits that couldn't be read: NOT watched
}

export interface SquareOffStatus {
  account_id: string;
  now: string;
  warn_from: string; // "15:00", India
  cutoffs: Record<string, string>; // by exchange: when the broker squares off MIS
  window_open: boolean;
  positions: { instrument_id: string; qty: number; cutoff: string | null; minutes_left: number | null; pnl_paise: number }[];
}

// --- research (K11): recordings and backtests ---------------------------------------------------

export interface RecordedDay {
  day: string;
  ticks: number;
  bytes: number;
  segments: Record<string, number>; // segment -> bytes on disk
  instruments: number;
  deletable?: boolean; // this user may delete it now (an admin; not today, still being written)
  replay?: string; // the command that replays it, where this server's days can be replayed
}

export interface Recordings {
  keep_days: number;
  days: RecordedDay[]; // newest first
}

export interface RecordedDayDetail extends Omit<RecordedDay, "instruments"> {
  instruments: Record<string, number>; // instrument -> ticks
}

/** What the backtest form offers, from the server (GET /api/research/backtests/form). */
export interface BacktestForm {
  algos: AlgoSpec[];
  days: string[]; // recorded days, newest first
  products: string[];
  candles: boolean; // candle-based runs are possible on this server
  candle_days?: string[]; // for the instrument asked about
  defaults: { product: string; start: string; capital_paise: number };
  max_days: number;
}

export interface BacktestSpecView {
  days: string[];
  algo: string;
  instrument_id: string;
  side: Side;
  qty: number;
  params: Record<string, unknown>;
  product: string;
  start: string;
  capital_paise: number;
  name: string;
}

export interface BacktestSummary {
  days_run: number;
  days_skipped: string[];
  gross_paise: number;
  charges_paise: number;
  net_paise: number;
  max_drawdown_paise: number;
  winning_days: number;
  win_rate: number | null;
  trades: number;
  equity_curve: [string, number][]; // (day, cumulative net)
}

/** A day of a run: the K10.3 report plus its intraday curve, or why it was skipped. */
export type BacktestDay = (DayReport & { equity_curve: [string, number][]; algo_started: boolean }) | { day: string; skipped: string };

export type BacktestState = "queued" | "running" | "done" | "failed";

export interface BacktestRun {
  run_id: string;
  spec: BacktestSpecView;
  candle_based: boolean;
  state: BacktestState;
  progress: number; // days done
  days: number;
  error: string;
  created_at: string;
  summary?: BacktestSummary;
  daily?: BacktestDay[]; // only when one run is asked for
}

// --- charts (K12): candles and the account's fills as marks -----------------------------------

/** One candle: prices in paise; `t` its start in India time (ISO). */
export interface Candle {
  t: string;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
  oi: number | null;
}

export interface ChartInterval {
  code: string;
  label: string;
  seconds: number;
  max_range_days: number;
  default_days: number;
}

export interface ChartSpec {
  intervals: ChartInterval[];
  history: string | null; // where past candles come from
  stream_every_s: number;
}

export interface CandlesAnswer {
  instrument_id: string;
  interval: string;
  candles: Candle[];
  live_from: string | null;
  note: string | null; // why history is missing, if it is
}

export interface ChartMark {
  t: string;
  side: Side;
  qty: number;
  price_paise: number;
  client_order_id: string;
  strategy: string;
}

// --- market alerts (K12.3) and market screens (K12.4) ----------------------------------------

/** What the alert form offers (GET /api/alerts/spec): kinds in the algo-spec format. */
export interface AlertSpec {
  kinds: AlgoSpec[];
  repeat: string[];
  cooldown_s: { min: number; max: number; default: number };
  max_alerts: number;
  note_max: number;
}

export interface MarketAlert {
  alert_id: string;
  user_id: string;
  kind: string;
  instrument_id: string;
  params: Record<string, unknown>;
  repeat: string;
  cooldown_s: number;
  note: string;
  webhook: boolean;
  enabled: boolean;
  created_at: string;
  armed: boolean;
  done: boolean;
  active: boolean;
  fired_count: number;
  last_fired_at: string | null;
  last_text: string;
  status: string;
  webhook_error: string;
  webhook_ok_at: string | null;
}

export interface WebhookSettings {
  url: string | null;
  set: boolean;
  secret?: string; // only in the answer that sets it
}

export interface Print {
  seq: number;
  t: string | null;
  price_paise: number;
  qty: number;
  side: "BUY" | "SELL" | "";
}

export interface PrintsAnswer {
  instrument_id: string;
  prints: Print[];
  kept: number;
  watched: boolean;
  estimate: string;
}

export interface MoverRow {
  instrument_id: string;
  symbol: string;
  ltp_paise: number;
  change_pct: number | null;
  volume: number | null;
  oi: number | null;
  oi_change_pct: number | null;
}

export interface MoversAnswer {
  by: "gainers" | "losers" | "volume" | "oi";
  rows: MoverRow[];
  of: number;
  note: string;
  exchanges: string[];
  kinds: string[];
}

// --- admin (K13.1/K13.2: /api/admin/…) ------------------------------------------------------------

export type RoleName = "admin" | "trader" | "risk";

export interface AdminUser {
  id: string;
  name: string;
  roles: RoleName[];
  active: boolean;
  must_change_password: boolean;
  locked_until: string | null; // failed logins: locked until then
  failures: number;
  sessions: number; // logged in on this many browsers now
}

/** A new user, or a reset: shown once, never again (the server keeps only hashes). */
export interface UserCreated {
  user: AdminUser;
  temporary_password: string;
  totp_uri: string;
  note: string;
}

export interface AdminAccount {
  id: string;
  broker: string;
  broker_client_id: string;
  owner: string;
  traders: string[];
  active: boolean;
  running: boolean;
  not_running: string; // why not, when it isn't
}

export interface AdminAccounts {
  accounts: AdminAccount[];
  brokers: string[]; // what this server can add
}

/** The limits spec: the algo-spec format, with the kinds money, choices and instruments. */
export interface LimitsSpec {
  code: string;
  name: string;
  help: string;
  family: string;
  params: AlgoParamSpec[];
}

export type LimitValue = number | boolean | string[] | null;

export interface LimitsAnswer {
  account_id: string;
  limits: Record<string, LimitValue>;
  running: boolean;
}

export interface LimitChange {
  name: string;
  label: string;
  old: LimitValue;
  new: LimitValue;
  text: string; // "Most lots per order: 10 → 5"
  looser: boolean;
}

export interface LimitsSaved extends LimitsAnswer {
  changes: LimitChange[];
  warnings: string[];
  applied: boolean;
}
