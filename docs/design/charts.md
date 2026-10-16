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
