# The browser stream (`/ws`)

One WebSocket per browser tab carries live prices, orders, positions, kill switch changes
and position alerts. Everything is JSON text frames. Money is in whole **paise**.

## Connecting

- URL: `wss://<server>/ws` (same origin as the page).
- The browser sends the session cookie automatically. The handshake is refused (HTTP 403)
  if the `Origin` isn't ours or there is no valid session.
- First message from the server: `hello`.

## After every (re)connect

The stream carries **changes**. A page that (re)connects must load the current state:

1. Open the WebSocket, wait for `hello`, and keep any messages that arrive.
2. Load snapshots: `GET /api/accounts/{a}/orders` and `GET /api/accounts/{a}/positions`.
3. Merge: for an order keep the version with the newest `updated_at`; for a position,
   the newest `updated_at`.
4. Re-send `subscribe` for the instruments on screen.

This makes reconnects lossless: nothing is ever silently missing.

## Messages from the browser

| Message | Meaning |
|---|---|
| `{"op": "subscribe", "instruments": ["NFO:NIFTY26OCTFUT", ...]}` | Start prices for these. The current price comes at once. At most `max_instruments` per connection (200). |
| `{"op": "unsubscribe", "instruments": [...]}` | Stop prices for these. |
| `{"op": "ping"}` | Answered with `{"type": "pong"}`. |

## Messages from the server

### `hello`
```json
{"type": "hello", "user": "ayush", "accounts": ["acc-ayush"], "flush_ms": 250, "max_instruments": 200}
```

### `ticks`: sent at once; in a burst at most one per instrument per `flush_ms`, newest wins
```json
{"type": "ticks", "ticks": [{
  "instrument_id": "NFO:NIFTY26OCTFUT", "ltp_paise": 2501030, "last_qty": 75,
  "volume": 1234500, "avg_price_paise": 2500870, "buy_qty": 150000, "sell_qty": 162000,
  "open_paise": 2498000, "high_paise": 2503500, "low_paise": 2496000, "close_paise": 2497500,
  "open_interest": 1500000, "exchange_time": "2026-10-14T10:15:02+05:30",
  "bids": [[2501000, 150, 2], [2500990, 300, 4]],
  "asks": [[2501010, 75, 1], [2501020, 150, 3]]
}]}
```
Depth levels are `[price_paise, qty, orders]`, best first. Fields a feed doesn't give are `null`.

### `order`: never conflated, never dropped, in order
```json
{"type": "order", "account_id": "acc-ayush", "kind": "UPDATED", "detail": "",
 "order": {"request": {...}, "broker_order_id": "251014000123", "status": "PARTIAL",
           "filled_qty": 25, "avg_price_paise": 2501000, "reason": "", "pending": "NONE",
           "created_at": "...", "updated_at": "..."},
 "fill": null}
```
`kind` is one of `SUBMITTED, REJECTED, SENT, UNCONFIRMED, UPDATED, FILL, MODIFY_SENT,
CANCEL_SENT, CANCELLED_LOCALLY, EXTERNAL`. A `FILL` carries
`"fill": {"client_order_id", "qty", "price_paise", "exact"}` (`exact` is false when the
broker gave no fill price and it was worked out from averages). `order` uses the same
format as `GET /api/accounts/{a}/orders` and the event log.

### `positions`: conflated per position, newest wins
```json
{"type": "positions", "account_id": "acc-ayush", "day_pnl_paise": 22500,
 "positions": [{"instrument_id": "NFO:NIFTY26OCTFUT", "product": "NRML", "qty": 225,
                "avg_price_paise": "2501000", "ltp_paise": 2501100, "realised_paise": 0,
                "unrealised_paise": 22500, "pnl_paise": 22500, "buy_qty": 225, "sell_qty": 0,
                "buy_value_paise": 562725000, "sell_value_paise": 0, "updated_at": "..."}]}
```
`avg_price_paise` is a decimal string (an average like 10007.5 paise isn't whole).

### `kill`: sent at once
```json
{"type": "kill", "scope": "account acc-ayush", "engaged": true, "reason": "taking a break",
 "by": "ayush", "at": "2026-10-14T10:20:00+00:00"}
```
`scope` is `"firm"` or `"account <id>"`.

### `position_alert`: sent at once
```json
{"type": "position_alert", "account_id": "acc-ayush", "state": "raised",
 "instrument_id": "NFO:NIFTY26OCTFUT", "ours": 75, "broker": 150,
 "message": "NFO:NIFTY26OCTFUT: Laddrix shows +75, the broker shows +150 (...)"}
```
`state` is `"raised"` or `"cleared"`.

### `error`
`{"type": "error", "message": "unknown instruments: ['NSE:NOPE']"}`: the connection stays open.

## Close codes

| Code | Meaning | What the page does |
|---|---|---|
| 1000 | normal | nothing |
| 4401 | not logged in (logout, expiry, disabled user) | show the login screen |
| 4403 | wrong origin | (never for our own page) |
| 4408 | too slow: the browser couldn't keep up | reconnect and reload snapshots |

## Limits

- Prices and positions: sent **at once** when nothing was sent in the last 250 ms; in a burst,
  at most one update per instrument / position every 250 ms (the newest).
- Measured (14 Oct 2026, localhost, real server and WebSocket): a lone tick reaches the browser
  in **1–6 ms** (it used to wait for a fixed 250 ms timer: ~200 ms). Kite sends about one tick per
  second per instrument, so nearly every tick takes the fast path.
- 2000 queued messages per connection; past that the connection is closed (4408),
  never silently skipped.
- The session is re-checked every 60 s.
