"""Decode Kite's binary WebSocket frames into ticks keyed by Kite's instrument token.

Frame layout (all integers big-endian, prices in paise for equity and F&O):

    [packet count: uint16]
    then for each packet: [length: uint16][packet bytes]

The packet length tells the mode:
    8    ltp            token, last price
    28   index quote    token, ltp, high, low, open, close, change
    32   index full     index quote + exchange timestamp
    44   quote          token, ltp, last qty, avg price, volume, buy qty, sell qty, open, high,
                        low, close
    184  full           quote + last trade time, OI, OI day high/low, exchange timestamp, and
                        10 depth entries (5 bids then 5 asks) of [qty uint32, price uint32,
                        orders uint16, 2 padding bytes]

The low byte of the token is the segment. Currency segments use other price scales;
Laddrix doesn't trade them, so their packets are skipped.
A frame shorter than 2 bytes is a heartbeat.
"""

import struct
from datetime import UTC, datetime
from functools import lru_cache
from typing import NamedTuple

from laddrix.domain.ticks import DepthLevel

SEGMENT_INDICES = 9
SEGMENTS_WITH_OTHER_PRICE_SCALES = frozenset({3, 6, 12})  # cds, bcd, nco

LTP_LEN, INDEX_QUOTE_LEN, INDEX_FULL_LEN, QUOTE_LEN, FULL_LEN = 8, 28, 32, 44, 184

_U16 = struct.Struct(">H")
_LTP = struct.Struct(">II")
_INDEX = struct.Struct(">IIIIIIi")  # token, ltp, high, low, open, close, change (signed)
_QUOTE = struct.Struct(">11I")
_FULL = struct.Struct(">16I" + "IIH2x" * 10)  # the whole 184-byte packet in one unpack
_U32 = struct.Struct(">I")
_new = tuple.__new__


class DecodeError(ValueError):
    """A frame or packet didn't match Kite's documented layout."""


class KiteTick(NamedTuple):
    """One decoded packet, still in Kite's terms (instrument token).

    A NamedTuple: immutable, and cheap to make (thousands a second; see metrics.py)."""

    token: int
    mode: str  # "ltp", "quote" or "full"
    ltp_paise: int
    last_qty: int | None = None
    avg_price_paise: int | None = None
    volume: int | None = None
    buy_qty: int | None = None
    sell_qty: int | None = None
    open_paise: int | None = None
    high_paise: int | None = None
    low_paise: int | None = None
    close_paise: int | None = None
    open_interest: int | None = None
    last_trade_time: datetime | None = None
    exchange_time: datetime | None = None
    bids: tuple[DepthLevel, ...] = ()
    asks: tuple[DepthLevel, ...] = ()


def split_packets(frame: bytes) -> list[bytes]:
    """Split one binary frame into its packets. Heartbeats give an empty list."""
    if len(frame) < 2:
        return []
    (count,) = _U16.unpack_from(frame, 0)
    packets: list[bytes] = []
    offset = 2
    for index in range(count):
        if offset + 2 > len(frame):
            raise DecodeError(f"frame ends before packet {index + 1} of {count}")
        (length,) = _U16.unpack_from(frame, offset)
        start, end = offset + 2, offset + 2 + length
        if end > len(frame):
            raise DecodeError(f"packet {index + 1} claims {length} bytes, frame is too short")
        packets.append(frame[start:end])
        offset = end
    return packets


def decode_packet(packet: bytes) -> KiteTick | None:
    """Decode one packet. Returns None for segments Laddrix doesn't handle."""
    if len(packet) < 4:
        raise DecodeError(f"packet of {len(packet)} bytes is too short")
    (token,) = _U32.unpack_from(packet, 0)
    segment = token & 0xFF
    if segment in SEGMENTS_WITH_OTHER_PRICE_SCALES:
        return None

    size = len(packet)
    if size == LTP_LEN:
        _, ltp = _LTP.unpack(packet)
        return KiteTick(token=token, mode="ltp", ltp_paise=ltp)

    if size in (INDEX_QUOTE_LEN, INDEX_FULL_LEN):
        _, ltp, high, low, open_, close, _change = _INDEX.unpack_from(packet, 0)
        exchange_time = _time(_U32.unpack_from(packet, 28)[0]) if size == INDEX_FULL_LEN else None
        return KiteTick(
            token=token,
            mode="full" if size == INDEX_FULL_LEN else "quote",
            ltp_paise=ltp,
            open_paise=open_,
            high_paise=high,
            low_paise=low,
            close_paise=close,
            exchange_time=exchange_time,
        )

    if size == QUOTE_LEN:
        (_, ltp, last_qty, avg, volume, buy_qty, sell_qty, open_, high, low, close) = _QUOTE.unpack(
            packet
        )
        return KiteTick(
            token=token,
            mode="quote",
            ltp_paise=ltp,
            last_qty=last_qty,
            avg_price_paise=avg,
            volume=volume,
            buy_qty=buy_qty,
            sell_qty=sell_qty,
            open_paise=open_,
            high_paise=high,
            low_paise=low,
            close_paise=close,
        )

    if size == FULL_LEN:
        v = _FULL.unpack(packet)
        (
            _,
            ltp,
            last_qty,
            avg,
            volume,
            buy_qty,
            sell_qty,
            open_,
            high,
            low,
            close,
            last_trade_time,
            oi,
            _oi_high,
            _oi_low,
            exchange_time,
        ) = v[:16]
        # depth entries are [qty, price, orders]; ours are (price, qty, orders). Built with
        # tuple.__new__ straight from the unpacked values: 5x cheaper than DepthLevel(...)
        levels = [_new(DepthLevel, e) for e in zip(v[17:46:3], v[16:46:3], v[18:46:3], strict=True)]
        return KiteTick(  # positional, in field order: the hot path
            token,
            "full",
            ltp,
            last_qty,
            avg,
            volume,
            buy_qty,
            sell_qty,
            open_,
            high,
            low,
            close,
            oi,
            _time(last_trade_time),
            _time(exchange_time),
            tuple(levels[:5]),
            tuple(levels[5:]),
        )

    raise DecodeError(f"unknown packet length {size}")


def decode_frame(frame: bytes) -> list[KiteTick]:
    """Decode a whole frame into ticks, skipping packets Laddrix doesn't handle."""
    return decode_packets(split_packets(frame))


def decode_packets(packets: list[bytes]) -> list[KiteTick]:
    ticks = []
    for packet in packets:
        tick = decode_packet(packet)
        if tick is not None:
            ticks.append(tick)
    return ticks


@lru_cache(maxsize=256)  # a frame's ticks share a few distinct seconds
def _time(seconds: int) -> datetime | None:
    """Kite sends Unix seconds; 0 means 'not available'."""
    return datetime.fromtimestamp(seconds, UTC) if seconds else None
