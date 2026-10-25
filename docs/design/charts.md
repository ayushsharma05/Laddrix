# Charts & alerts (K12)

## K12.1 Candles and the chart data API (#231)

```
MarketData.tap ─► CandleBuilder.on_tick ─► 1-minute candles per instrument (one update a tick)
                                            │ added up on request: 3m 5m 15m 30m 1h 1d
CandleHistory (replaceable)                 │
  KiteHistory     /instruments/historical ──┼─► Charts.candles: each bucket once
  RecordedHistory the recorded ticks ───────┘     (live where the builder saw it whole)
```

- **One level built live.** Only 1-minute candles are kept per instrument (about four trading days). Bigger intervals are added up when a chart asks, so a tick costs one dict lookup and one candle update.
- **Buckets** are India time, aligned to the session open (09:15 for NSE/BSE/NFO/BFO, kept as data). 15-minute candles fall at 09:15, 09:30…; hourly at 09:15, 10:15… (as Kite's are). A day candle is the India date. Pre-open times stay on the same grid.
- **Volume** is the change in the day's total volume, never `last_qty`; the same rule as volume at price.
  - The first tick seen is a baseline.
  - A total that goes down starts a new day.
  - A tick with no new volume and the same price is a quote and adds nothing. A quote in a new minute opens no candle.
  - Instruments with no volume (indices) still draw prices.
- **Late ticks** are ignored and counted (`out_of_order`).
- **History joined to live, each bucket once.** The builder's first minute is partial (it started mid-minute), so it reports `complete_from`.
  - A bucket that starts at or after the builder saw it whole comes from the builder, which is fresher than a history read.
  - Earlier buckets come from history.
  - A bucket only one side has comes from that side.
  - The answer says `live_from`. If history fails, its reason goes in `note` and the live candles still come.
- **Kite history** (`charts/history.py`): `GET /instruments/historical/{token}/{interval}` with `oi=1` and `continuous=0`.
  - Ranges are split to the per-interval limit.
  - Requests are spaced to at most 3 a second.
  - Finished days are kept in memory.
  - The request goes through the account that carries prices (`KiteLive.request`).
  - Historical data is included in the ₹500/month Kite Connect plan. **The per-interval range limits and the 3/s rate come from Kite's forum, not its docs: check them before going live.**
- **Paper and demo history** come from the recordings (K11.1) through the same builder. Finished days are cached; today's is rebuilt at most once a minute, because the recording grows and the live builder has the newest minutes anyway.

### API

| | |
|---|---|
| `GET /api/charts/spec` | intervals (code, label, seconds, `max_range_days`, `default_days`), the history source, stream rate |
| `GET /api/charts/{instrument}/candles?interval=&start=&end=` | candles `{t, o, h, l, c, v, oi}` (paise; `t` India time), `live_from`, `note`; times ISO, no zone = India; default range ends now |
| `GET /api/accounts/{a}/charts/{instrument}/marks?start=&end=` | the account's fills in that instrument (each once; fills logged twice count once): time, side, qty, price, order, strategy |
| stream `{"op": "candles", "instrument_id", "interval"}` / `candles_stop` | `{"type": "candle", …, "candle"}` at once (null if none yet), then at most once a second while it changes; the instrument is watched meanwhile; at most 8 per connection |

Checks:
- `tests/test_charts.py`, a hand-worked minute, buckets, quotes, new day, OI, late ticks, aggregation, the join, Kite split/spacing/cache/parsing, recordings, marks, API and stream;
- `tests/test_kite_live.py` (history through the live account);
- `context/scripts/mutate-charts.py` (23/23);
- `context/scripts/check-charts.py` on a running demo. One complete minute from the API equalled the same minute worked by hand from the recorded ticks, and a real paper fill came back as a mark.


## K12.2 The chart widget (#232)

```
[1m 3m 5m 15m 30m 1h 1d]  NIFTY26OCTFUT  10-16 11:45 O … H … L … C … V 21K ▲ BUY 75 @ 25,009.40
┌──────────────────────────────────────────────┬──────────┐
│ candles: up hollow, down filled ▲buys ▼sells │ price    │  canvas; the readout above is
│ volume                                       │ axis     │  text (screen readers, tests)
└──────────────────────────────────────────────┴──────────┘
```

- **From the server only.**
  - Intervals come from `GET /api/charts/spec`, candles from `…/candles`, and the forming candle from the stream op `candles` (shared per instrument and interval, asked again after a reconnect).
  - Marks come from `…/charts/{i}/marks` and are read again after every fill.
  - Older pages load as the view nears the left edge, and stop when a page adds nothing.
  - An older page waits for the first load to answer, so there are never two history reads at once. An answer for an interval no longer shown is dropped.
- **Reading it.**
  - Drag or ←/→ pans; the wheel or +/− zooms (3–40 px a candle); Home or a double-click goes back to the newest.
  - Hovering shows the candle under the pointer: OHLCV, OI, and the fills in it.
  - The x axis counts candles, not time, so nights and holidays leave no gaps; a new day shows its date.
  - Prices snap to the instrument's tick on the crosshair and on a click.
- **Click a price → the linked ticket.** The link bus (`workspace/links.ts`) carries a picked price with a sequence number and a time.
  - The ticket on the same link group and instrument fills in the price and switches to a priced type if it was on Market. It says "Price … from the chart: check it, then send".
  - A pick never sends an order: the ticket's own confirm and gate still apply.
  - A ticket that switches instrument for the pick takes it if it is under 5 s old.
  - An unlinked chart says how to link it.
- **Colours.** Green/red failed the colour-blind check (ΔE 5.3 for deutan). The candles use the validated blue (up) and orange (down) pair, and up candles are hollow while down ones are filled, so shape tells them apart too.
- **Found by the browser check and fixed:**
  - A drag crashed the chart after the release: the pan update read the drag state after it had been cleared.
  - An older page asked for one interval could land after switching to another.
  - History was read twice at once, with no answer for over 2.4 s.
  - The tab titles of linked widgets didn't follow their instrument. This was already happening for the ladder and the ticket; the tab now listens to the title change.

Checks:
- `ui/src/widgets/chart/*.test.ts(x)` cover the model, the drawing (a recording 2D context) and the widget (load, stream, older pages, races, marks, picks, drag, keys);
- the ticket and stream tests, and `ui/src/workspace/Panel.test.tsx`;
- `context/scripts/mutate-chart-ui.py` (32/32);
- `context/scripts/e2e-chart.cjs`, a real browser on the demo. It checked:
  - canvas pixels painted, and the forming candle moving;
  - a real fill marked;
  - a click filling the ticket's price exactly;
  - tabs renamed;
  - zoom showing 8 of 28 candles;
  - a drag panning back 5 minutes;
  - no page errors.

**A spread's chart (#429).** A spread (`SPREAD:…`) charts too: its candles are worked out
from its legs' candles, with gaps where a leg had none and high/low only approximate, and the
chart says "spread mark, not traded prices". See
[spreads.md](spreads.md#7-spread-charts-429).


## K12.3 Market alerts and webhooks (#233)

```
MarketData.tap ─► AlertEngine.on_tick ─► the alerts on that instrument (an index)
                                          ─► crossed? fired ─► audit ALERT_FIRED
                                                             ─► the user's screens (stream)
                                                             ─► their webhook (if asked), signed
REST create/change/delete ─► AlertStore: one JSON file per user (alerts + webhook), saved
                             every second off the loop
```

- **Kinds**, published by `GET /api/alerts/spec` in the algo-spec format, so the screen draws the form and the server checks with the same description:
  - price crosses above / below a level;
  - moves X% from the previous close (up / down / either);
  - day volume above;
  - open interest changes X% from when the alert armed;
  - spread (A − B) crosses above / below a level, evaluated when either leg ticks.
- **Crossing, not touching.** An alert must first see its condition false (armed); it fires when the condition becomes true. A gap straight through the level counts, and exactly at the level counts.
  - An alert made when the condition is already true says "already true: fires the next time it crosses" instead of firing at once.
  - "Once" alerts are then done. "Every time" alerts arm again once the condition is false again, and wait out their cool-down (10 s to a day).
  - After a restart, kept alerts start disarmed, so a crossing while the server was down doesn't fire late.
- **Prices for alerts.** The engine keeps a market-data subscription on every instrument an alert needs, so Kite streams it even when no screen shows it.
- **Each user's own.** `/api/alerts` lists, creates (201), changes (PATCH) and deletes (204) only the caller's alerts; another user's are 404. At most 200 each. Fired alerts and their state go only to that user's screens (stream `market_alert`, `market_alert_state`). Every change is audited (`ALERT_*`).
- **Webhooks** (`/api/alerts/webhook` PUT/GET/DELETE, `…/test`):
  - The URL must be https with a public host. IP literals and anything resolving to private, loopback, link-local or metadata addresses are refused, so a webhook can't reach the server's own network.
  - The secret is shown once, when the URL is set.
  - Each POST carries `X-Laddrix-Timestamp` and `X-Laddrix-Signature: sha256=HMAC(secret, "<timestamp>.<body>")`. The receiver recomputes the signature and refuses old timestamps, so a copied request can't be replayed.
  - Each send has a 5 s timeout and is tried up to 4 times (1/2/4 s apart) on network errors, 429 and 5xx. A 4xx or a redirect is not retried. At most 500 sends wait at once.
  - The result shows on the alert (`webhook_ok_at`, `webhook_error`).
  - **The checked address is the one connected to (#271).** Each try looks the host up, refuses it unless every answer is public, then connects to that address. The TLS certificate is still checked against the host name. So a DNS answer that changes between the check and the connection (rebinding) can't reach a private address.
  - **Also:**
    - redirects are not followed;
    - IPv4 written as IPv6 (`::ffff:10.0.0.1`) is judged as IPv4;
    - multicast is refused;
    - no connection is reused for another host name;
    - at most 500 sends wait and 20 connect at once ("test" sends included).
  - **Known limit:** a proxy-only network, where the server can't resolve names itself, refuses every webhook (fail closed).

Checks:
- `tests/test_market_alerts.py` (22):
  - each kind worked by hand (exact level, gap through, already true, cool-down, a new day's volume, the OI baseline, either spread leg);
  - checks, the index, a restart, any user id as a safe file name;
  - webhook URL rules, the signature, retries, refusals, private resolution, the send cap;
  - the API per user, webhook routes, the stream to that user only, instruments kept watched;
- `context/scripts/mutate-alerts.py` (26/26);
- `context/scripts/check-alerts.py` on a running demo. An alert just above the live price fired on the stream about 30 s later; already-true alerts said so; the audit rows were written. A real outside webhook couldn't be sent from the build sandbox (no direct DNS); use the test button once deployed.

## K12.4 Alerts manager, time & sales, depth and movers (#234)

Four screens, each fed by the server; the UI ranks, estimates and checks nothing on its own.

- **Alerts manager** (`alerts` widget): the user's alerts with their state ("watching", "already true…", "fired …", off), and a form drawn from `GET /api/alerts/spec` (the same parameter inputs as the algo screen). Turn on/off, delete, set or test the webhook (its secret is shown once). A fired alert updates its row from the stream and adds a Messages line "Alert: …". Click a row: the link group switches to that contract.
- **Time & sales** (`tape` widget, `GET /api/instruments/{id}/prints?after=&limit=`):
  - Kite sends snapshots, not single trades. A "print" is what traded between two snapshots: the volume difference, at the newer last price and last-trade time. Two trades in one snapshot show as one.
  - The side is estimated from the touch *before* the trade: at or above the previous best ask is BUY, at or below the previous best bid is SELL, in between is unknown. The screen says "sides estimated".
  - Kept only for contracts some screen watches (the newest 300 each), so the work is a dict lookup per tick. A falling total volume means a new day and starts a new baseline.
  - The screen asks every 1 s for the prints after the newest one it has. It filters by minimum quantity and shows the bought and sold totals of what it shows.
- **Depth** (`depth` widget): the 5 levels Kite sends, with bars (share of the largest level), running totals, the whole book's waiting quantity when sent, the imbalance and the spread in paise and ticks. Straight from the tick stream.
- **Movers** (`movers` widget, `GET /api/market/movers?by=gainers|losers|volume|oi&limit=&exchange=&kind=`):
  - Ranked by the server from the latest tick of every contract it has a price for: % from the previous close, the day's volume, or the biggest open-interest move either way since the first OI seen today (Kite's ticks carry no previous-day OI, and the screen says so).
  - It answers with the exchanges and kinds it has, so the filters list only those. It polls every 2 s; a new tab or filter asks at once.

Two fixes found while checking these screens in a browser:
- **A ticket that switched contract kept the old contract's price** (a bug from before K12.2). For one render after the switch, `useTick`, `useInstrument` and `useOrderSpec` still returned the previous contract's values, and the ticket started its price from them. Now each hook returns a value only when it is for the contract (and account) asked; otherwise the cached one for the new contract, or nothing. Test: "switching contract starts the price at the new contract's price, never the old one's".
- **Simulator options showed absurd % changes.** Their day started at a placeholder price, and only their trading price came from the option model. They now open and close around the model price.

Checks:
- **What these screens' numbers are (#277):**
  - **Time and sales:** every field is an estimate, and the screen says so ("estimated from price snapshots", "Qty ≈", "Side ≈"):
    - the quantity is the volume traded between two snapshots, so two trades in one show as one;
    - the time is the snapshot's last-trade time;
    - the side is inferred from the touch before the trade.
  - **Restarts:** the numbering starts again at 1 each time the server starts, so each run carries an `epoch`. A screen that sees a new epoch starts its tape again. Before #277, a screen open across a restart asked for "after 500" and stayed empty until 500 new trades had passed.
  - **Movers** rank only the contracts with a live price on this server (those a screen, an order or an algo watches), never the whole exchange. The answer says so (`universe`), and the screen reads "of N watched here, not the whole market".
- `tests/test_market_screens.py`: a hand-worked tape (BUY at the ask, SELL at the bid, unknown inside, nothing traded), a new day, unwatched contracts, the size kept; movers' ranking, ties, filters, limits, the OI day baseline; the API.
- UI tests in `ui/src/widgets/market` and `ui/src/widgets/alerts`, the hook tests in `ui/src/app/context.test.tsx`, and the ticket test above.
- `context/scripts/mutate-market.py` (39/39 caught: server, simulator, models, widgets, hooks).
- `context/scripts/e2e-market.cjs` on the demo in a browser: depth, tape, movers, the linked ticket at the new contract's price after a Movers click, creating and firing an alert, no page errors.
