"""Render a tick's 5-level depth as a text ladder (used by the terminal watcher and tests)."""

from laddrix.domain.money import to_rupees
from laddrix.domain.ticks import Tick


def render_ladder(tick: Tick) -> str:
    """Asks above (highest first), bids below (highest first), LTP in the header."""
    lines = [
        f"{tick.instrument_id}  LTP {to_rupees(tick.ltp_paise):,.2f}",
        "   bid qty |    price   | ask qty",
    ]
    for level in reversed([lvl for lvl in tick.asks if lvl.qty]):
        lines.append(f"{'':>10} | {to_rupees(level.price_paise):>10,.2f} | {level.qty:<8}")
    lines.append("-" * 36)
    for level in (lvl for lvl in tick.bids if lvl.qty):
        lines.append(f"{level.qty:>10} | {to_rupees(level.price_paise):>10,.2f} |")
    return "\n".join(lines)
