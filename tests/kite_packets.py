"""Build Kite binary packets for tests, exactly as documented (big-endian, paise)."""

import struct

NSE_TOKEN = 738561  # low byte 1 = NSE segment
NFO_TOKEN = 13368834  # low byte 2 = NFO segment
INDEX_TOKEN = 256265  # low byte 9 = indices (NIFTY 50)
CDS_TOKEN = 412675  # low byte 3 = currency segment, skipped by MockTrade


def ltp_packet(token: int, ltp: int) -> bytes:
    return struct.pack(">II", token, ltp)


def quote_packet(
    token: int,
    ltp: int,
    *,
    last_qty=10,
    avg=250000,
    volume=12345,
    buy_qty=500,
    sell_qty=600,
    open_=249000,
    high=252000,
    low=248000,
    close=249500,
) -> bytes:
    return struct.pack(
        ">11I", token, ltp, last_qty, avg, volume, buy_qty, sell_qty, open_, high, low, close
    )


def full_packet(
    token: int,
    ltp: int,
    *,
    bids=(),
    asks=(),
    last_trade_time=1760420000,
    exchange_time=1760420001,
    oi=0,
) -> bytes:
    head = quote_packet(token, ltp) + struct.pack(">5I", last_trade_time, oi, 0, 0, exchange_time)
    levels = list(bids)[:5] + [(0, 0, 0)] * (5 - len(list(bids)[:5]))
    levels += list(asks)[:5] + [(0, 0, 0)] * (5 - len(list(asks)[:5]))
    depth = b"".join(struct.pack(">IIH2x", qty, price, orders) for price, qty, orders in levels)
    return head + depth


def index_packet(token: int, ltp: int, *, full: bool = False) -> bytes:
    body = struct.pack(">IIIIIIi", token, ltp, 2510000, 2490000, 2495000, 2500000, 1000)
    return body + (struct.pack(">I", 1760420001) if full else b"")


def frame(*packets: bytes) -> bytes:
    out = struct.pack(">H", len(packets))
    for p in packets:
        out += struct.pack(">H", len(p)) + p
    return out
