"""P&L over many days, from kept end-of-day reports: by day, by instrument, by strategy.

Each day's P&L is that day's move (carried positions start at the previous close), so adding
days gives the P&L of the period. A day without a kept report is listed as missing, never
counted as zero silently.
"""

from datetime import date, timedelta
from typing import Any

BY = ("day", "instrument", "strategy")


def pnl_history(reports: list[dict[str, Any]], by: str, first: date, last: date,
                trading_days: list[date] | None = None) -> dict[str, Any]:  # fmt: skip
    if by not in BY:
        raise ValueError(f"by must be one of {', '.join(BY)}")
    rows: dict[str, dict[str, int]] = {}

    def add(key: str, gross: int, charges: int, trades: int = 0) -> None:
        row = rows.setdefault(key, {"gross_paise": 0, "charges_paise": 0, "trades": 0})
        row["gross_paise"] += gross
        row["charges_paise"] += charges
        row["trades"] += trades

    for r in reports:
        if by == "day":
            t = r["totals"]
            add(r["day"], t["gross_paise"], t["charges_paise"], t["trades"])
        elif by == "instrument":
            for p in r["positions"]:
                add(p["instrument_id"], p["gross_paise"], p["charges_paise"])
            for t in r["trades"]:
                rows.setdefault(t["instrument_id"], {"gross_paise": 0, "charges_paise": 0,
                                                     "trades": 0})["trades"] += 1  # fmt: skip
        else:
            for s in r["strategies"]:
                add(s["strategy"], s["gross_paise"], s["charges_paise"], s["trades"])
    kept = {r["day"] for r in reports}
    days = trading_days if trading_days is not None else _weekdays(first, last)
    out = [{"key": k, **v, "net_paise": v["gross_paise"] - v["charges_paise"]}
           for k, v in sorted(rows.items())]  # fmt: skip
    return {
        "by": by,
        "from": first.isoformat(),
        "to": last.isoformat(),
        "rows": out,
        "total": {
            "gross_paise": sum(r["gross_paise"] for r in out),
            "charges_paise": sum(r["charges_paise"] for r in out),
            "net_paise": sum(r["net_paise"] for r in out),
        },
        "missing_days": [d.isoformat() for d in days if d.isoformat() not in kept],
    }


def _weekdays(first: date, last: date) -> list[date]:
    days, d = [], first
    while d <= last:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days
