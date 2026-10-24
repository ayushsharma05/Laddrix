"""Histogram math with known samples, and the two ways metrics are read."""

import pytest

from mocktrade.metrics import BOUNDS, Histogram, Metrics


def test_bounds_are_1_2_5_per_decade():
    assert BOUNDS[:7] == [1, 2, 5, 10, 20, 50, 100]
    assert BOUNDS[-1] >= 1e9  # up to 1000 s in µs


def test_an_empty_histogram_says_nothing():
    h = Histogram("x", "us")
    assert h.quantile(0.5) is None
    assert h.snapshot() == {
        "unit": "us",
        "help": "",
        "count": 0,
        "mean": None,
        "p50": None,
        "p90": None,
        "p99": None,
        "max": None,
    }


def test_one_value_is_every_quantile():
    h = Histogram("x", "us")
    h.observe(37)
    assert h.quantile(0.01) == h.quantile(0.5) == h.quantile(0.99) == 37


def test_uniform_1_to_1000_gives_quantiles_within_their_bucket():
    h = Histogram("x", "us")
    for v in range(1, 1001):
        h.observe(v)
    assert h.count == 1000 and h.max == 1000 and h.min == 1
    assert h.total == sum(range(1, 1001))
    # true p50 = 500 lies in the (200, 500] bucket; true p99 = 990 in (500, 1000]
    assert 200 < h.quantile(0.5) <= 500
    assert 500 < h.quantile(0.99) <= 1000
    # estimates are within one bucket of the truth and never beyond the max seen
    assert h.quantile(1.0) == 1000


def test_a_tail_shows_in_p99_not_p50():
    h = Histogram("x", "us")
    for _ in range(98):
        h.observe(10)
    h.observe(5000)
    h.observe(9000)
    assert h.quantile(0.5) == pytest.approx(10, abs=5)
    assert h.quantile(0.99) > 2000
    assert h.max == 9000


def test_bad_readings_are_ignored():
    h = Histogram("x", "us")
    h.observe(-1)
    h.observe(float("nan"))
    assert h.count == 0


def test_snapshot_and_prometheus_text():
    m = Metrics()
    m.observe("kite_frame_us", 12)
    m.observe("http_us{route=/api/orders}", 800)
    m.inc("kite_ticks", 3)
    m.gauge("connections", lambda: 2)
    m.gauge("broken", lambda: 1 / 0)
    snap = m.snapshot()
    assert snap["histograms"]["kite_frame_us"]["count"] == 1
    assert snap["counters"] == {"kite_ticks": 3}
    assert snap["gauges"] == {"broken": None, "connections": 2}  # a failing gauge reads None
    text = m.prometheus()
    assert 'mocktrade_http_us_bucket{route="/api/orders",le="1000"} 1' in text
    assert 'mocktrade_http_us_bucket{route="/api/orders",le="500"} 0' in text
    assert "mocktrade_kite_frame_us_count 1" in text
    assert "mocktrade_kite_ticks_total 3" in text
    assert "mocktrade_connections 2" in text
    m.reset()
    assert m.snapshot()["histograms"]["kite_frame_us"]["count"] == 0


def test_prometheus_says_each_metrics_type_once_before_its_samples():
    """Prometheus refuses a scrape with a second TYPE line for a metric (#287)."""
    m = Metrics()
    m.observe("http_us{route=GET /a}", 5)
    m.observe("http_us{route=GET /b}", 7)
    m.inc("http_responses{status=2xx}")
    m.inc("http_responses{status=5xx}")
    m.gauge("ops_alerts{level=WARN}", lambda: 1)
    m.gauge("ops_alerts{level=ERROR}", lambda: 0)
    lines = m.prometheus().splitlines()
    types = [line.split()[2] for line in lines if line.startswith("# TYPE")]
    assert sorted(types) == ["mocktrade_http_responses_total", "mocktrade_http_us",
                             "mocktrade_ops_alerts"]  # fmt: skip
    for metric in types:
        typed_at = next(i for i, x in enumerate(lines) if x.startswith(f"# TYPE {metric} "))
        first_sample = next(i for i, x in enumerate(lines) if x.startswith(metric))
        assert typed_at < first_sample
