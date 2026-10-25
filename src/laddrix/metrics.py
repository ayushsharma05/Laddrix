"""Measurements: how long each step takes, as histograms, so speed is a number, not a guess.

    tick path     Kite frame ─► decoded + fanned out (kite_frame_us)
                  tick arrived ─► on the browser's wire (stream_tick_age_us)
    order path    browser request ─► answered (http_us{route})
                  sent to the broker ─► broker's acknowledgement (broker_place_ms{broker})
    everything    event loop lag (loop_lag_ms): how late the server is running anything

A histogram keeps counts in fixed 1-2-5 buckets (1, 2, 5, 10, 20, 50 ...), so recording is a
few hundred nanoseconds and memory never grows; quantiles (p50, p99) are estimated inside
their bucket. Reading: GET /api/metrics (JSON, admin and risk only), or ?format=prometheus
for a monitoring system.

One registry (METRICS) for the process. Recording never raises and never blocks.
"""

import asyncio
import bisect
import gc
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


def _bounds(top: float = 1e9) -> list[float]:
    """1, 2, 5, 10, 20, 50, ... up to `top`."""
    out, decade = [], 1.0
    while decade <= top:
        for m in (1, 2, 5):
            out.append(m * decade)
        decade *= 10
    return out


BOUNDS = _bounds()


@dataclass
class Histogram:
    name: str
    unit: str  # "us", "ms"
    help: str = ""
    counts: list[int] = field(default_factory=lambda: [0] * (len(BOUNDS) + 1))
    count: int = 0
    total: float = 0.0
    min: float = math.inf
    max: float = 0.0

    def observe(self, value: float) -> None:
        if value < 0 or value != value:  # negative or NaN: a clock oddity, not a measurement
            return
        self.counts[bisect.bisect_left(BOUNDS, value)] += 1
        self.count += 1
        self.total += value
        self.min = min(self.min, value)
        self.max = max(self.max, value)

    def quantile(self, q: float) -> float | None:
        """The value below which a fraction `q` of observations fall (bucket-interpolated)."""
        if not self.count:
            return None
        rank = q * self.count
        seen = 0
        for i, n in enumerate(self.counts):
            if not n:
                continue
            if seen + n >= rank:
                lo = BOUNDS[i - 1] if i > 0 else 0.0
                hi = BOUNDS[i] if i < len(BOUNDS) else self.max
                lo, hi = max(lo, self.min), min(hi, self.max)
                return lo + (hi - lo) * ((rank - seen) / n)
            seen += n
        return self.max

    def snapshot(self) -> dict[str, Any]:
        def r(v: float | None) -> float | None:
            return None if v is None else round(v, 3)

        return {
            "unit": self.unit,
            "help": self.help,
            "count": self.count,
            "mean": r(self.total / self.count) if self.count else None,
            "p50": r(self.quantile(0.50)),
            "p90": r(self.quantile(0.90)),
            "p99": r(self.quantile(0.99)),
            "max": r(self.max) if self.count else None,
        }

    def reset(self) -> None:
        self.counts = [0] * (len(BOUNDS) + 1)
        self.count, self.total, self.min, self.max = 0, 0.0, math.inf, 0.0


class Metrics:
    def __init__(self) -> None:
        self._histograms: dict[str, Histogram] = {}
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, Callable[[], float]] = {}
        self.started = time.monotonic()

    def histogram(self, name: str, unit: str = "us", help: str = "") -> Histogram:
        found = self._histograms.get(name)
        if found is None:
            found = self._histograms[name] = Histogram(name, unit, help)
        return found

    def observe(self, name: str, value: float, unit: str = "us") -> None:
        self.histogram(name, unit).observe(value)

    def inc(self, name: str, by: int = 1) -> None:
        self._counters[name] = self._counters.get(name, 0) + by

    def gauge(self, name: str, read: Callable[[], float]) -> None:
        """A value read when metrics are read (connections, CPU, ...)."""
        self._gauges[name] = read

    def reset(self) -> None:
        for h in self._histograms.values():
            h.reset()
        self._counters.clear()

    def snapshot(self) -> dict[str, Any]:
        gauges: dict[str, float | None] = {}
        for name, read in sorted(self._gauges.items()):
            try:
                gauges[name] = read()
            except Exception:
                gauges[name] = None
        return {
            "uptime_s": round(time.monotonic() - self.started, 1),
            "histograms": {n: h.snapshot() for n, h in sorted(self._histograms.items())},
            "counters": dict(sorted(self._counters.items())),
            "gauges": gauges,
        }

    def prometheus(self) -> str:
        """The Prometheus text format, for a monitoring system."""
        lines = []
        typed: set[str] = set()

        def kind(metric: str, of: str) -> None:
            """One TYPE line per metric, before its first sample: Prometheus refuses a
            second one (http_us{route=...} has a sample for every route)."""
            if metric not in typed:
                typed.add(metric)
                lines.append(f"# TYPE {metric} {of}")

        for name, h in sorted(self._histograms.items()):
            metric, labels = _split(name)
            kind(f"laddrix_{metric}", "histogram")
            cumulative = 0
            for bound, n in zip(BOUNDS, h.counts, strict=False):
                cumulative += n
                lines.append(
                    f"laddrix_{metric}_bucket{_labels(labels, le=_num(bound))} {cumulative}"
                )
            lines.append(f"laddrix_{metric}_bucket{_labels(labels, le='+Inf')} {h.count}")
            lines.append(f"laddrix_{metric}_sum{_labels(labels)} {h.total}")
            lines.append(f"laddrix_{metric}_count{_labels(labels)} {h.count}")
        for name, value in sorted(self._counters.items()):
            metric, labels = _split(name)
            kind(f"laddrix_{metric}_total", "counter")
            lines.append(f"laddrix_{metric}_total{_labels(labels)} {value}")
        for name, value in self.snapshot()["gauges"].items():
            if value is not None:
                metric, labels = _split(name)
                kind(f"laddrix_{metric}", "gauge")
                lines.append(f"laddrix_{metric}{_labels(labels)} {value}")
        return "\n".join(lines) + "\n"


def _split(name: str) -> tuple[str, str]:
    """'http_us{route=/api/x}' -> ('http_us', 'route=/api/x')."""
    if "{" in name and name.endswith("}"):
        base, labels = name[:-1].split("{", 1)
        return base, labels
    return name, ""


def _labels(raw: str, **extra: str) -> str:
    parts = []
    if raw:
        for pair in raw.split(","):
            key, _, value = pair.partition("=")
            value = value.replace("\\", "\\\\").replace('"', '\\"')
            parts.append(f'{key}="{value}"')
    parts += [f'{k}="{v}"' for k, v in extra.items()]
    return "{" + ",".join(parts) + "}" if parts else ""


def _num(v: float) -> str:
    return str(int(v)) if v == int(v) else str(v)


METRICS = Metrics()


def since_us(start_ns: int) -> float:
    """Microseconds since a time.perf_counter_ns() reading."""
    return (time.perf_counter_ns() - start_ns) / 1000


def process_gauges(metrics: Metrics) -> None:
    """CPU time and memory of this process (Linux /proc; elsewhere they read None), and how
    long Python's garbage collector stops everything (gc_pause_ms{gen=0|1|2})."""
    metrics.gauge("process_cpu_seconds", lambda: round(time.process_time(), 3))
    metrics.gauge("process_rss_mb", _rss_mb)
    if _gc_timer not in gc.callbacks:
        gc.callbacks.append(_gc_timer)


_gc_started = 0


def freeze_heap() -> None:
    """Move everything alive now (instruments, settings, code) out of the garbage collector's
    sight. Full collections otherwise scan it all again and again: about 100 ms per pause
    with 1,000 contracts (measured), far more with Kite's 90,000. Call once start-up (or the
    daily instrument load) is done."""
    gc.collect()
    gc.freeze()


def _gc_timer(phase: str, info: dict) -> None:
    global _gc_started
    if phase == "start":
        _gc_started = time.perf_counter_ns()
    elif _gc_started:
        METRICS.observe(
            f"gc_pause_ms{{gen={info.get('generation')}}}", since_us(_gc_started) / 1000, "ms"
        )
        _gc_started = 0


def _rss_mb() -> float:
    with open("/proc/self/statm") as f:
        pages = int(f.read().split()[1])
    return round(pages * os.sysconf("SC_PAGE_SIZE") / 2**20, 1)


async def watch_loop_lag(metrics: Metrics = METRICS, interval_s: float = 0.1) -> None:
    """How late the event loop wakes up: everything (ticks, orders, screens) waits that long."""
    while True:
        started = time.perf_counter()
        await asyncio.sleep(interval_s)
        late = time.perf_counter() - started - interval_s
        metrics.observe("loop_lag_ms", max(late, 0.0) * 1000, "ms")
