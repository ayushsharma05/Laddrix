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
