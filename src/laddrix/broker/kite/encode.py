"""Build Kite binary frames (big-endian, paise), exactly as Kite sends them.

For simulators and load tests that must go through the real decoder; nothing in the live
path uses this.
"""

import struct

NFO_SEGMENT = 2  # a token's low byte is its segment


def nfo_token(n: int) -> int:
    return (n << 8) | NFO_SEGMENT


def full_packet(
    token: int,
    ltp: int,
    *,
    volume: int = 0,
    bids: tuple[tuple[int, int, int], ...] = (),  # (price, qty, orders)
    asks: tuple[tuple[int, int, int], ...] = (),
    exchange_time: int = 0,
) -> bytes:
    """A 184-byte "full" packet: quote fields, times, open interest and 5 + 5 depth."""
    head = struct.pack(">11I", token, ltp, 1, ltp, volume, 0, 0, ltp, ltp, ltp, ltp) + struct.pack(
        ">5I", exchange_time, 0, 0, 0, exchange_time
    )
    levels = list(bids)[:5] + [(0, 0, 0)] * (5 - len(list(bids)[:5]))
    levels += list(asks)[:5] + [(0, 0, 0)] * (5 - len(list(asks)[:5]))
    depth = b"".join(struct.pack(">IIH2x", qty, price, orders) for price, qty, orders in levels)
    return head + depth


def frame(packets: list[bytes]) -> bytes:
    out = [struct.pack(">H", len(packets))]
    for p in packets:
        out.append(struct.pack(">H", len(p)) + p)
    return b"".join(out)
