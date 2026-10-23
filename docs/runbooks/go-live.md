# Go-live checklist (V1.4, #330)

Use this before the first live session, and again after any change to orders, recovery, protection or the Kite adapter. Each step says how to check it and what **pass** looks like.

There are two columns of evidence, and they are never mixed up:

- **Paper**: the paper broker filling against simulated prices. It proves MockTrade's own logic. **It proves nothing about Kite.** Run it with `python3 context/scripts/golive-paper.py`; the last run is in `context/evidence/v1-4-golive-paper.txt` (19/19).
- **Live**: a real Kite account on the Mumbai server. **Only Ayush runs these steps. No live order is placed without his explicit go-ahead.** The live steps use the smallest size the exchange allows, prefer a cheap, liquid stock (one share) to a futures lot, and are run in market hours. Record the date, time and outcome in the last column of each table.

The steps marked "test" point to automated tests that check the same logic against a fake Kite. Those are not live evidence either.

## 0. Before the day

| Step | Pass | Paper / test | Live (Ayush) |
|---|---|---|---|
| Server set up as in [server.md](server.md): static IP, hardening, HTTPS | `https://<name>/api/health` answers; SSH only from home | — | ☐ |
| Kite app's IP whitelist has the server's static IP (SEBI: orders only from a registered IP) | `server.md` § "Check that orders work": the laptop is refused, the server isn't | — | ☐ |
| `MOCKTRADE_SECRET_KEY`, `deploy/.env` and the Kite API secret are in the password manager | you can read all three from it | — | ☐ |
| Backups run and a restore works | `server.md` § Restore, step 1 (into `/tmp/check`) succeeds on last night's backup | drill 19/19: `context/evidence/v1-3-backup-restore-drill.txt` | ☐ |
| A crash loses nothing | `incidents.md` § Crash-and-restart drill | drill 17/17 (#328) | — (never crash a live server on purpose) |
| Holidays set | `MOCKTRADE_HOLIDAYS` has NSE's list for the year | — | ☐ |
| Alerts reach your phone | set `MOCKTRADE_ALERT_WEBHOOK`, restart, stop the price feed briefly (or wait for the 08:30 login reminder): the message arrives | private and plain-http addresses refused (paper) | ☐ |
| Limits are right | Admin → Limits: max order value, max lots, daily loss, OPS ≤ 10/s | — | ☐ |
| SEBI algo rules re-checked (#291) | `docs/design/compliance.md`: the 10 orders/s line and the static IP still hold; if any algo runs above 10/s, it is registered, `MOCKTRADE_ALGO_IDS` set, and Kite's field name for the algo id confirmed with Zerodha | algos refuse to start above 10/s without an id (tests) | ☐ |

## 1. Login and session expiry

| Step | Pass | Paper / test | Live (Ayush) |
|---|---|---|---|
| Before login, orders are refused | the strip says **Log in to Kite**; an order is refused with that reason | test: `test_kite_live.py::test_before_login_orders_are_refused_and_the_screen_is_told` | ☐ |
| Log in from the screen | Zerodha's page, then the strip turns green; prices start; no restart | test: `…::test_login_from_the_screen_goes_live_without_a_restart` | ☐ |
| A restart during the day keeps the login | restart the app: still logged in, the instrument list from the cache | test: `…::test_a_restart_during_the_day_uses_the_saved_login_and_the_cached_list` | ☐ (outside market hours) |
| The next morning, yesterday's login is not used | at 06:00 the strip says Log in to Kite again; the 08:30 reminder arrives | test: `…::test_yesterdays_login_is_not_used`, `test_kite_ticker.py::test_expired_login_stops_retrying` | ☐ |

## 2. Orders, cancels, modifies and the broker check

Before each live order: check the size, the price and the account in the ticket. Use a limit away from the market so it rests, and one share.

| Step | Pass | Paper | Live (Ayush) |
|---|---|---|---|
| A limit order rests | OPEN in the order book, and in Kite's own order book with the same price and quantity | ✔ rests at 2% under | ☐ |
| Modify price and quantity | both show in MockTrade and in Kite | ✔ 150 at the new price | ☐ |
| Cancel | CANCELLED in both | ✔ | ☐ |
| A refused order says why | off the tick grid or not a whole lot: refused in words, before it reaches Kite | ✔ both | ☐ (optional: MockTrade refuses it first) |
| A market order fills (only with the go-ahead: it trades) | FILLED; the position shows; `market_protection` was set | ✔ +75 | ☐ |
| The broker check agrees | Positions → "broker now": no POSITION DIFFERS band for 3 checks (about 10 minutes) | ✔ `ok: true`, no alerts | ☐ |
| A trade made in Kite's own app is noticed | POSITION DIFFERS appears within two checks; nothing is adopted silently (`incidents.md` § Reconcile mismatch) | — | ☐ |

## 3. Market-data interruptions

| Step | Pass | Paper / test | Live (Ayush) |
|---|---|---|---|
| Prices stop | **PRICES n s OLD** band within 5 s; after a minute the "No prices" alert (and your phone) | ✔ band on a frozen server (#328 screenshots) | ☐ (pull the network for 30 s, outside an open position) |
| Prices come back | the band and the alert clear by themselves; subscriptions are restored | ✔ band cleared on resume | ☐ |
| Reconnects back off and resubscribe | — | test: `test_kite_ticker.py::test_reconnects_with_backoff_and_resubscribes` | (seen in the step above) |

## 4. Protective orders

| Step | Pass | Paper | Live (Ayush) |
|---|---|---|---|
| A stop-loss order (SL-M) on the closing side rests | in Kite's order book as TRIGGER PENDING | ✔ rests, cancelled | ☐ |
| A GTT stop is placed and deleted | listed in Kite's GTT list as active, then deleted | ✔ placed active, deleted | ☐ |
| A stop held by MockTrade (trailing stop, OCO, bracket) is understood | its help text says "works while the server runs"; a restart raises **UNPROTECTED** (`incidents.md`) | ✔ crash drill (#328) | ☐ (read it; don't crash on purpose) |

## 5. Square-off

| Step | Pass | Paper | Live (Ayush) |
|---|---|---|---|
| Square-off asks first | without confirming it is refused | ✔ | ☐ |
| It closes intraday (MIS) positions only | MIS position closed; NRML left alone | ✔ +75 MIS closed, NRML kept | ☐ (only with an MIS position you mean to close) |
| Before the broker's cut-off the warning shows | from 15:00 a band lists the open MIS positions and the minutes left | test: `test_mtm.py::test_square_off_window_warning_and_one_click_closes_only_mis`, `…::test_day_guards_raise_and_clear_band_alerts` | ☐ (watch it once) |

## 6. Alert webhooks (yours, per user)

| Step | Pass | Paper | Live (Ayush) |
|---|---|---|---|
| Unsafe addresses are refused | plain http, localhost and private addresses are refused in words | ✔ three refused; test: `test_market_alerts.py::test_webhook_addresses_must_be_public_https` | — |
| A real delivery arrives | Alerts → webhook: an https address (for example ntfy.sh/<your-topic>) → **Test**: the message arrives on your phone | — (the test machine can't reach the internet) | ☐ |
| A market alert fires | an alert just above the price fires once and is delivered | test: `test_market_alerts.py::test_crossing_above_fires_once_at_the_level_and_through_a_gap` | ☐ |

## Sign-off

The core v1 gate (#240) needs this table filled in by Ayush: the date, and each **Live** box ticked or marked "not done: why". Paper evidence on its own never ticks a live box.
