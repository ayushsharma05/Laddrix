"""The option chain: every strike of one underlying and expiry, priced and with Greeks.

Backend provides, frontend displays: the server works out the forward, the ATM strike, IV and
Greeks for every row, PCR and max pain; the chain screen only draws them.

    expiries("NIFTY")                    -> listed expiry dates, nearest first
    snapshot("NIFTY", expiry, around=15) -> the ATM strike +- 15 strikes, both sides
    watch(...)                           -> keeps those contracts subscribed (moving with ATM)

Change in OI is measured from the first OI seen today for that contract (Kite's feed has no
previous-day OI); the screen labels it so.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from laddrix.domain.instruments import Instrument, InstrumentId, InstrumentKind
from laddrix.marketdata.service import MarketData, Subscription
from laddrix.options.model import (
    DEFAULT_RATE,
    IST,
    forward_for,
    mid_or_last,
    option_quote,
    time_to_expiry,
)
from laddrix.refdata.expiry import Expiries, expiry_to_dict
from laddrix.refdata.store import InstrumentStore

K = InstrumentKind


@dataclass
class _Series:
    """One underlying + expiry: its strikes and contracts."""

    strikes: list[int]  # paise, ascending
    calls: dict[int, Instrument]
    puts: dict[int, Instrument]
    futures: list[Instrument]


class ChainService:
    def __init__(
        self,
        store: InstrumentStore,
        market_data: MarketData,
        *,
        rate: float = DEFAULT_RATE,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._md = market_data
        self._rate = rate
        self._clock = clock
        self._first_oi: dict[InstrumentId, tuple[date, int]] = {}  # today's first OI
        self._expiries = Expiries(store, clock=clock)  # expiry warnings (#290)

    # --- what is listed ---

    def underlyings(self) -> list[str]:
        return self._store.underlyings()

    def expiries(self, underlying: str) -> list[date]:
        now = self._clock()  # an expiry has gone once its contracts expired at 15:30 (#290)
        return sorted(
            {
                i.expiry
                for i in self._store.derivatives(underlying)
                if i.kind in (K.CALL, K.PUT) and i.expiry is not None
                and not self._expiries.expired(i, now)
            }
        )  # fmt: skip

    def _series(self, underlying: str, expiry: date) -> _Series:
        calls: dict[int, Instrument] = {}
        puts: dict[int, Instrument] = {}
        futures: list[Instrument] = []
        for i in self._store.derivatives(underlying):
            if i.kind is K.FUTURE:
                futures.append(i)
            elif i.expiry == expiry and i.strike_paise is not None:
                (calls if i.kind is K.CALL else puts)[i.strike_paise] = i
        return _Series(sorted(set(calls) | set(puts)), calls, puts, futures)

    # --- prices ---

    def _price(self, instrument_id: InstrumentId) -> float | None:
        return mid_or_last(self._md.latest(instrument_id))

    def forward(self, underlying: str, expiry: date) -> float | None:
        series = self._series(underlying, expiry)
        any_option = next(iter(series.calls.values() or series.puts.values()), None)
        if any_option is None:
            return None
        return forward_for(any_option, series.futures, self._price, self._clock(), self._rate)

    def _window(self, series: _Series, atm: int | None, around: int) -> list[int]:
        if not series.strikes:
            return []
        if atm is None:
            middle = len(series.strikes) // 2
        else:
            middle = series.strikes.index(atm)
        lo, hi = max(0, middle - around), min(len(series.strikes), middle + around + 1)
        return series.strikes[lo:hi]

    def contracts(self, underlying: str, expiry: date, around: int) -> list[InstrumentId]:
        """What a chain of ATM +- `around` strikes needs streaming: its options and futures."""
        series = self._series(underlying, expiry)
        forward = self.forward(underlying, expiry)
        atm = _nearest(series.strikes, forward) if forward else None
        ids = [f.id for f in series.futures]
        for strike in self._window(series, atm, around):
            for side in (series.calls, series.puts):
                if strike in side:
                    ids.append(side[strike].id)
        return ids

    def snapshot(self, underlying: str, expiry: date, around: int = 15) -> dict[str, Any]:
        now = self._clock()
        series = self._series(underlying, expiry)
        forward = self.forward(underlying, expiry)
        atm = _nearest(series.strikes, forward) if forward else None
        rows: list[dict[str, Any]] = []
        for strike in self._window(series, atm, around):
            rows.append(
                {
                    "strike_paise": strike,
                    "call": self._leg(series.calls.get(strike), forward, now),
                    "put": self._leg(series.puts.get(strike), forward, now),
                }
            )
        call_oi = sum(r["call"]["oi"] or 0 for r in rows if r["call"])
        put_oi = sum(r["put"]["oi"] or 0 for r in rows if r["put"])
        atm_row = next((r for r in rows if r["strike_paise"] == atm), None)
        atm_ivs = [
            leg["iv"] for leg in (atm_row or {}).values() if isinstance(leg, dict) and leg["iv"]
        ]
        any_option = next(iter([*series.calls.values(), *series.puts.values()]), None)
        expiry_info = self._expiries.info(any_option, now) if any_option else None
        return {
            "underlying": underlying,
            "expiry": expiry.isoformat(),
            "expiry_info": expiry_to_dict(expiry_info),  # expires today? how it settles
            "forward_paise": round(forward) if forward else None,
            "atm_strike_paise": atm,
            "days_to_expiry": round(time_to_expiry(expiry, now) * 365, 2),
            "rate": self._rate,
            "pcr": round(put_oi / call_oi, 3) if call_oi else None,
            "max_pain_paise": max_pain(rows),
            "atm_iv": round(sum(atm_ivs) / len(atm_ivs), 6) if atm_ivs else None,
            "rows": rows,
        }

    def _leg(self, option: Instrument | None, forward: float | None, now: datetime) -> dict | None:
        if option is None:
            return None
        tick = self._md.latest(option.id)
        price = mid_or_last(tick)
        quote = option_quote(option, price, forward, now, self._rate)
        oi = tick.open_interest if tick else None
        g = quote.greeks
        bid = tick.best_bid if tick else None
        ask = tick.best_ask if tick else None
        return {
            "instrument_id": option.id,
            "ltp_paise": tick.ltp_paise if tick else None,
            "bid_paise": bid.price_paise if bid else None,
            "ask_paise": ask.price_paise if ask else None,
            "volume": tick.volume if tick else None,
            "oi": oi,
            "oi_change": self._oi_change(option.id, oi, now),
            "iv": round(quote.iv, 6) if quote.iv is not None else None,
            "delta": round(g.delta, 4) if g else None,
            "gamma": round(g.gamma, 8) if g else None,
            "theta": round(g.theta, 4) if g else None,  # rupees per calendar day
            "vega": round(g.vega, 4) if g else None,  # rupees per IV point
        }

    def _oi_change(self, instrument_id: InstrumentId, oi: int | None, now: datetime) -> int | None:
        if oi is None:
            return None
        today = now.astimezone(IST).date()
        first = self._first_oi.get(instrument_id)
        if first is None or first[0] != today:
            self._first_oi[instrument_id] = (today, oi)
            return 0
        return oi - first[1]

    # --- keeping a chain's contracts streaming ---

    def watch(self, underlying: str, expiry: date, around: int) -> "ChainWatch":
        return ChainWatch(self, underlying, expiry, around)


class ChainWatch:
    """A chain's contracts kept subscribed; `refresh()` follows the ATM as the market moves."""

    def __init__(self, service: ChainService, underlying: str, expiry: date, around: int):
        self.service, self.underlying, self.expiry, self.around = (
            service,
            underlying,
            expiry,
            around,
        )
        self.subscription: Subscription = service._md.subscribe(())
        self.refresh()

    def refresh(self) -> None:
        wanted = set(self.service.contracts(self.underlying, self.expiry, self.around))
        current = self.subscription.instrument_ids
        self.subscription.remove(current - wanted)
        self.subscription.add(wanted - current)

    def snapshot(self) -> dict[str, Any]:
        self.subscription.drain()
        self.refresh()
        return self.service.snapshot(self.underlying, self.expiry, self.around)

    def close(self) -> None:
        self.subscription.close()


def _nearest(strikes: list[int], forward: float | None) -> int | None:
    if not strikes or forward is None:
        return None
    return min(strikes, key=lambda k: (abs(k - forward), k))


def max_pain(rows: list[dict]) -> int | None:
    """The expiry price at which option holders, all together, would collect the least."""
    strikes = [r["strike_paise"] for r in rows]
    if not strikes:
        return None
    best, best_pay = None, None
    for s in strikes:
        pay = 0
        for r in rows:
            k = r["strike_paise"]
            if r["call"] and r["call"]["oi"]:
                pay += r["call"]["oi"] * max(0, s - k)
            if r["put"] and r["put"]["oi"]:
                pay += r["put"]["oi"] * max(0, k - s)
        if best_pay is None or pay < best_pay:
            best, best_pay = s, pay
    return best
