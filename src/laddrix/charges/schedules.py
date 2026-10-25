"""Charge schedules: rates as data, with the date they were checked.

`KITE_STANDARD` is the default for Kite and paper accounts. A server can load another table
from a JSON file (LADDRIX_CHARGES) without a code change, e.g. after a budget:

    {"name": "...", "checked_on": "2026-10-15", "source": "https://...",
     "sebi_pct": "0.0001", "gst_pct": "18",
     "segments": {"futures": {"brokerage_pct": "0.03", "brokerage_cap_paise": 2000,
                              "stt_sell_pct": "0.05", "exchange_pct": {"NSE": "0.00173"},
                              "stamp_buy_pct": "0.002", "ipft_pct": {"NSE": "0.0001"}}, ...}}

Percentages are written as text ("0.05" means 0.05%) so they stay exact.

Checked 15 Oct 2026, against the broker's published charges and news of the 2026 budget
(STT on F&O from 1 April 2026: futures 0.05% of the sell value, options 0.15% of the sell
premium). Sources disagree on the exchange and IPFT rates (by a few rupees a crore): check
them against the broker's charges page before trading real money, and update this table.
"""

import json
from datetime import date
from fractions import Fraction
from pathlib import Path
from typing import Any

from laddrix.charges.model import ChargeSchedule, Segment, SegmentRates


def pct(text: str) -> Fraction:
    return Fraction(text)


KITE_STANDARD = ChargeSchedule(
    name="Kite standard plan",
    checked_on=date(2026, 10, 15),
    source="the broker's published charges page; STT: Finance Act 2026",
    segments={
        Segment.DELIVERY: SegmentRates(
            stt_buy_pct=pct("0.1"),
            stt_sell_pct=pct("0.1"),
            exchange_pct={"NSE": pct("0.00297"), "BSE": pct("0.00375")},
            stamp_buy_pct=pct("0.015"),
            ipft_pct={"NSE": pct("0.0001")},
            dp_paise=1534,  # ₹15.34 a stock a day, GST included
        ),
        Segment.INTRADAY: SegmentRates(
            brokerage_pct=pct("0.03"),
            brokerage_cap_paise=2000,
            stt_sell_pct=pct("0.025"),
            exchange_pct={"NSE": pct("0.00297"), "BSE": pct("0.00375")},
            stamp_buy_pct=pct("0.003"),
            ipft_pct={"NSE": pct("0.0001")},
        ),
        Segment.FUTURES: SegmentRates(
            brokerage_pct=pct("0.03"),
            brokerage_cap_paise=2000,
            stt_sell_pct=pct("0.05"),
            exchange_pct={"NSE": pct("0.00173"), "BSE": pct("0")},
            stamp_buy_pct=pct("0.002"),
            ipft_pct={"NSE": pct("0.0001")},
        ),
        Segment.OPTIONS: SegmentRates(
            brokerage_flat_paise=2000,
            stt_sell_pct=pct("0.15"),
            exchange_pct={"NSE": pct("0.03503"), "BSE": pct("0.0325")},
            stamp_buy_pct=pct("0.003"),
            ipft_pct={"NSE": pct("0.0005")},
        ),
    },
)


def schedule_from_dict(data: dict[str, Any]) -> ChargeSchedule:
    """Raises ValueError, naming the field, for anything it can't read."""
    try:
        segments = {}
        for name, rates in data["segments"].items():
            segment = Segment(name)
            segments[segment] = SegmentRates(
                brokerage_pct=pct(str(rates.get("brokerage_pct", "0"))),
                brokerage_cap_paise=_int_or_none(rates.get("brokerage_cap_paise")),
                brokerage_flat_paise=_int_or_none(rates.get("brokerage_flat_paise")),
                stt_buy_pct=pct(str(rates.get("stt_buy_pct", "0"))),
                stt_sell_pct=pct(str(rates.get("stt_sell_pct", "0"))),
                exchange_pct={k: pct(str(v)) for k, v in rates.get("exchange_pct", {}).items()},
                stamp_buy_pct=pct(str(rates.get("stamp_buy_pct", "0"))),
                ipft_pct={k: pct(str(v)) for k, v in rates.get("ipft_pct", {}).items()},
                dp_paise=int(rates.get("dp_paise", 0)),
            )
        schedule = ChargeSchedule(
            name=str(data["name"]),
            checked_on=date.fromisoformat(data["checked_on"]),
            source=str(data.get("source", "")),
            segments=segments,
            sebi_pct=pct(str(data.get("sebi_pct", "0.0001"))),
            gst_pct=pct(str(data.get("gst_pct", "18"))),
        )
    except (KeyError, ValueError, TypeError, ZeroDivisionError) as exc:
        raise ValueError(f"charge schedule: can't read {exc}") from None
    missing = [s.value for s in Segment if s not in schedule.segments]
    if missing:
        raise ValueError(f"charge schedule: no rates for {', '.join(missing)}")
    return schedule


def load_schedule(path: str | Path) -> ChargeSchedule:
    return schedule_from_dict(json.loads(Path(path).read_text()))


def schedule_to_dict(s: ChargeSchedule) -> dict[str, Any]:
    """What the server publishes (percentages as text, exactly as in the table)."""

    def text(value: Fraction) -> str:
        return format(value.numerator / value.denominator, "f").rstrip("0").rstrip(".") or "0"

    return {
        "name": s.name,
        "checked_on": s.checked_on.isoformat(),
        "source": s.source,
        "sebi_pct": text(s.sebi_pct),
        "gst_pct": text(s.gst_pct),
        "segments": {
            seg.value: {
                "brokerage_pct": text(r.brokerage_pct),
                "brokerage_cap_paise": r.brokerage_cap_paise,
                "brokerage_flat_paise": r.brokerage_flat_paise,
                "stt_buy_pct": text(r.stt_buy_pct),
                "stt_sell_pct": text(r.stt_sell_pct),
                "exchange_pct": {k: text(v) for k, v in r.exchange_pct.items()},
                "stamp_buy_pct": text(r.stamp_buy_pct),
                "ipft_pct": {k: text(v) for k, v in r.ipft_pct.items()},
                "dp_paise": r.dp_paise,
            }
            for seg, r in s.segments.items()
        },
    }


def _int_or_none(value: Any) -> int | None:
    return None if value is None else int(value)
