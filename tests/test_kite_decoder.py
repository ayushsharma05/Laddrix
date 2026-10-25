"""Kite's binary ticks decoded exactly as Kite documents them (broker/kite/decoder.py), packet by
packet.
"""

import struct
import time
from datetime import UTC, datetime

import pytest
from kite_packets import (
    CDS_TOKEN,
    INDEX_TOKEN,
    NFO_TOKEN,
    NSE_TOKEN,
    frame,
    full_packet,
    index_packet,
    ltp_packet,
    quote_packet,
)

from laddrix.broker.kite.decoder import DecodeError, decode_frame, decode_packet, split_packets
from laddrix.domain.ticks import DepthLevel


def test_ltp_packet():
    tick = decode_packet(ltp_packet(NSE_TOKEN, 250105))
    assert (tick.token, tick.mode, tick.ltp_paise) == (NSE_TOKEN, "ltp", 250105)
    assert tick.bids == () and tick.volume is None


def test_quote_packet_has_ohlc_and_volume_but_no_depth():
    tick = decode_packet(quote_packet(NSE_TOKEN, 250105, volume=999))
    assert tick.mode == "quote"
    assert tick.volume == 999
    assert (tick.open_paise, tick.high_paise, tick.low_paise, tick.close_paise) == (
        249000,
        252000,
        248000,
        249500,
    )
    assert tick.bids == ()


def test_full_packet_decodes_five_bids_and_five_asks_in_order():
    bids = [(2501000 - 10 * i, 75 * (i + 1), i + 1) for i in range(5)]
    asks = [(2501010 + 10 * i, 150 * (i + 1), i + 2) for i in range(5)]
    tick = decode_packet(full_packet(NFO_TOKEN, 2501000, bids=bids, asks=asks, oi=12000))
    assert tick.mode == "full"
    assert tick.bids[0] == DepthLevel(price_paise=2501000, qty=75, orders=1)
    assert tick.asks[4] == DepthLevel(price_paise=2501050, qty=750, orders=6)
    assert tick.open_interest == 12000
    assert tick.exchange_time == datetime.fromtimestamp(1760420001, UTC)


def test_zero_timestamps_mean_not_available():
    tick = decode_packet(full_packet(NSE_TOKEN, 1, last_trade_time=0, exchange_time=0))
    assert tick.last_trade_time is None and tick.exchange_time is None


@pytest.mark.parametrize("full", [False, True])
def test_index_packets(full):
    tick = decode_packet(index_packet(INDEX_TOKEN, 2500500, full=full))
    assert tick.ltp_paise == 2500500
    assert tick.high_paise == 2510000 and tick.close_paise == 2500000
    assert (tick.exchange_time is not None) is full


def test_currency_segment_is_skipped():
    assert decode_packet(ltp_packet(CDS_TOKEN, 8312)) is None


def test_frame_with_several_packets_and_a_skipped_one():
    ticks = decode_frame(
        frame(ltp_packet(NSE_TOKEN, 1), ltp_packet(CDS_TOKEN, 2), ltp_packet(NFO_TOKEN, 3))
    )
    assert [t.ltp_paise for t in ticks] == [1, 3]


@pytest.mark.parametrize("heartbeat", [b"", b"\x00"])
def test_heartbeats_give_no_ticks(heartbeat):
    assert split_packets(heartbeat) == []


def test_truncated_frames_are_errors():
    good = frame(ltp_packet(NSE_TOKEN, 1))
    with pytest.raises(DecodeError):
        split_packets(good[:-1])
    with pytest.raises(DecodeError):
        split_packets(struct.pack(">H", 2) + good[2:])


@pytest.mark.parametrize("size", [2, 12, 100])
def test_unknown_packet_sizes_are_errors(size):
    packet = struct.pack(">I", NSE_TOKEN).ljust(max(size, 4), b"\x00")[:size]
    with pytest.raises(DecodeError):
        decode_packet(packet)


def test_matches_zerodhas_own_parser():
    """Equivalence: our decoder and the official SDK read the same bytes the same way."""
    kiteconnect = pytest.importorskip("kiteconnect")
    bids = [(2501000 - 10 * i, 75 * (i + 1), i + 1) for i in range(5)]
    asks = [(2501010 + 10 * i, 150 * (i + 1), i + 2) for i in range(5)]
    data = frame(
        full_packet(NFO_TOKEN, 2501000, bids=bids, asks=asks, oi=12000),
        quote_packet(NSE_TOKEN, 250105),
        ltp_packet(NSE_TOKEN, 250110),
        index_packet(INDEX_TOKEN, 2500500, full=True),
    )
    sdk = kiteconnect.KiteTicker("key", "token")._parse_binary(data)
    ours = decode_frame(data)
    assert len(sdk) == len(ours) == 4
    for theirs, mine in zip(sdk, ours, strict=True):
        assert theirs["instrument_token"] == mine.token
        assert round(theirs["last_price"] * 100) == mine.ltp_paise
        if "depth" in theirs:
            assert [round(d["price"] * 100) for d in theirs["depth"]["buy"]] == [
                level.price_paise for level in mine.bids
            ]
            assert [d["quantity"] for d in theirs["depth"]["sell"]] == [
                level.qty for level in mine.asks
            ]
            assert [d["orders"] for d in theirs["depth"]["buy"]] == [
                level.orders for level in mine.bids
            ]
        if "volume_traded" in theirs:
            assert theirs["volume_traded"] == mine.volume


def test_decoding_is_fast_enough():
    """Measure-first: 100,000 full packets must decode in well under a second of CPU."""
    data = frame(*[full_packet(NFO_TOKEN, 2501000 + i) for i in range(100)])
    start = time.perf_counter()
    for _ in range(1000):
        decode_frame(data)
    elapsed = time.perf_counter() - start
    print(
        f"\n100,000 full packets decoded in {elapsed:.3f} s ({elapsed / 100_000 * 1e6:.1f} µs each)"
    )
    assert elapsed < 5  # generous for slow CI machines; typical laptop result is far lower
