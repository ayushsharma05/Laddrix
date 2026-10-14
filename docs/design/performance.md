# Performance: measured, not guessed

How fast MockTrade is, how it is measured, and what changed because of the measurements.

## How it is measured

- **`/api/metrics`** (admin and risk; add `?format=prometheus` for a monitoring system):
  histograms with p50, p90, p99 and max for every step (`src/mocktrade/metrics.py`).

  | Metric | What it times |
  |---|---|
  | `kite_frame_us` | a Kite frame from arrival to its ticks fanned out |
  | `kite_chunk_us` | the longest stretch the decoder holds the server (one 250-tick chunk) |
  | `kite_fanout_us` | handing a frame's ticks to market data and the screens |
  | `stream_tick_age_us` | a tick's arrival at the server to its place on a screen's socket (conflation waits included) |
  | `stream_send_us{type}` | any other message from its event to the socket |
  | `http_us{route}` | each API request, by route template |
  | `broker_ms{op,broker}` | place, modify and cancel round trips to the broker |
  | `loop_lag_ms` | how late the server runs anything |
  | `gc_pause_ms{gen}` | garbage-collector pauses |

  Gauges: connections, ticks received, contracts watched, CPU seconds, memory.
- **`python -m mocktrade.tools.loadtest`** runs the real server (`tools/serve.py`, kite mode)
  in its own process, fed by a **fake Kite** that sends real 184-byte "full" frames over a
  WebSocket and answers the REST calls. Screen-like WebSocket clients and HTTP orders run in a
  second process. Every round gives every contract a new price, so the clients time each
  tick from the moment Kite sent it. `--profile FILE` profiles the server process.

## Results (14 Oct 2026, shared cloud VM, 4 vCPU)

This VM is noisy: the same pure-Python loop varies by ±50% between runs. The figures are
medians of 3 runs of 30 s each. **Re-measure on the Mumbai server** (`docs/runbooks/server.md`).

**1,000 contracts × 2 ticks/s (Kite's busiest), 5 screens × 200 contracts, 2 orders/s plus
their cancels:**

| Measure | p50 | p99 |
|---|---|---|
| **Kite → screen, end to end** (decode, conflation, both sockets, the client's JSON parse) | 65 ms | 133 ms |
| **Order: click → server → Kite ack → screen** (fake Kite answers at once, so this is our own overhead) | 19 ms | 79 ms |
| KiteBroker → Kite REST ack (localhost) | 8 ms | – |
| Longest hold-up by one decode chunk (250 ticks) | – | 21 ms |
| Event loop lag | ~1 ms | 32 ms (max 44 ms) |
| Server CPU · memory | 13% of one core · 79 MB | |

**3,000 contracts × 1 tick/s, 10 screens × 200 contracts:** end to end p50 160 ms, p99 394 ms;
orders p50 21 ms; CPU 17% of one core; 94 MB.

With the real Kite, the network round trip from the server to Kite adds to the order
number. Measure it from Mumbai with `broker_ms{op=place,broker=kite}`. Prices arrive from Kite about once a second per contract, so a
tick of 65 ms is far fresher than the next one.

## What the measurements changed (same VM, same load)

| Step | Before | After | Change |
|---|---|---|---|
| Decode one Kite packet | 20.2 µs | 10.3 µs | one `struct` unpack for the whole packet; depth levels built with `tuple.__new__` |
| Kite tick → our tick → market data → subscribers | 13.2 µs | 3.8 µs | `Tick`, `KiteTick` and `DepthLevel` are NamedTuples (immutable, about 4× cheaper to create than frozen dataclasses), built positionally; one arrival time per frame |
| Encode a tick for the screens | 28.7 µs | 7.0 µs | `orjson`; each tick encoded once and shared by every screen watching it |
| **Per tick, total** | **62 µs** | **21 µs** | **about 3× faster** |
| Full garbage-collector pause | ~100 ms (3 in 15 s) | 10–20 ms | `gc.freeze()` after start-up and after each daily instrument load: long-lived objects are no longer rescanned. Kite's real list has about 90,000 contracts, so this matters even more there |
| Longest event-loop stall | 136 ms | 30–50 ms | GC freeze, plus the decoder yields every 250 ticks: a big frame never holds up an order click |
| A frame reaching the screens | in up to 4 parts, the later ones up to 250 ms late | in one part | decoded in chunks, but handed on whole |

## Known limits and next steps

- Python does about 21 µs of work per tick. At 3,000 contracts × 2 ticks/s that is about 13%
  of a core, so throughput has room to spare. Latency at that size comes from serial
  per-frame work. If needed, the decoder and encoder can move to a separate process (or to
  Rust) behind the same interfaces. Nothing else would change.
- Screens update at most 4 times a second (the stream's 250 ms throttle, see `stream.md`),
  by design, so a slow browser is never flooded. Orders and order updates are never
  throttled.
