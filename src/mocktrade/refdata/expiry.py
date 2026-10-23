"""Expiry day for futures and options (#290): when a contract ends, how it settles, and what
may still be ordered on it. The server publishes this; the screens only show it.

    contract ─► info(contract, now) ─► expires at 15:30 India time on its expiry date
                                     ─► settles in cash (index) or by delivery (stock)
                                     ─► expires today? expired? opening orders closed?
             ─► settlement_price(contract) ─► what a position is worth at the end:
                  a future: its last price; an option: what it is "in the money" by
                  (its strike against the underlying's price), never below zero

The rules, checked 23 Oct 2026 (re-check before trading real money; docs/design/expiry.md):
- Equity F&O contracts end at the market close, 15:30, on their expiry date.
- Index F&O (NIFTY, BANKNIFTY, SENSEX...) settle in cash.
- Stock F&O settle by delivery ("physical settlement"): an open future, or an option that
  ends in the money, becomes a trade in the shares themselves. Brokers add their own
  cut-offs before expiry (for example closing such positions themselves, or refusing new
  ones); those differ by broker and change, so they are settings here (`ExpiryRules`),
  not guesses.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from mocktrade.domain.instruments import Instrument, InstrumentKind
from mocktrade.refdata.store import InstrumentStore

IST = ZoneInfo("Asia/Kolkata")
EXPIRY_CLOSE = time(15, 30)  # the market close on the expiry date, India time
OPTIONS = (InstrumentKind.CALL, InstrumentKind.PUT)


@dataclass(frozen=True)
class ExpiryRules:
    """A broker's own cut-offs on the expiry date, India time. From this time, orders that
    open (or add to) a position are refused; closing trades still go. None: no cut-off."""

    physical_open_cutoff: time | None = None  # stock F&O (settled by delivery)
    cash_open_cutoff: time | None = None  # index F&O (settled in cash)


@dataclass(frozen=True)
class ExpiryInfo:
    """One contract's expiry, as the screens and the risk checks see it at a moment."""

    instrument_id: str
    expires_at: datetime  # 15:30 India time on the expiry date
    settlement: str  # "cash" or "physical"
    expires_today: bool
    expired: bool
    opening_cutoff: datetime | None  # from here, only closing trades
    opening_closed: bool
    note: str  # one sentence for a trader


class Expiries:
    """Expiry facts for the contracts in one instrument store."""

    def __init__(
        self,
        store: InstrumentStore,
        *,
        rules: ExpiryRules | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self.rules = rules or ExpiryRules()
        self._clock = clock

    def physical(self, instrument: Instrument) -> bool:
        """Settled by delivery: the underlying is a listed share (not an index)."""
        name = instrument.underlying
        if not name:
            return False
        return any(
            i.kind is InstrumentKind.EQUITY
            for i in (self._store.get(f"{x}:{name}") for x in ("NSE", "BSE"))
            if i is not None
        )

    def info(self, instrument: Instrument, now: datetime | None = None) -> ExpiryInfo | None:
        """None for anything that doesn't expire (shares, indices)."""
        if instrument.expiry is None:
            return None
        now = (now or self._clock()).astimezone(IST)
        expires_at = datetime.combine(instrument.expiry, EXPIRY_CLOSE, tzinfo=IST)
        physical = self.physical(instrument)
        cutoff_time = self.rules.physical_open_cutoff if physical else self.rules.cash_open_cutoff
        cutoff = (
            datetime.combine(instrument.expiry, cutoff_time, tzinfo=IST) if cutoff_time else None
        )
        expired = now >= expires_at
        today = now.date() == instrument.expiry
        return ExpiryInfo(
            instrument_id=instrument.id,
            expires_at=expires_at,
            settlement="physical" if physical else "cash",
            expires_today=today and not expired,
            expired=expired,
            opening_cutoff=cutoff,
            opening_closed=cutoff is not None and now >= cutoff,
            note=_note(instrument, expires_at, physical, cutoff, expired, today),
        )

    def expired(self, instrument: Instrument, now: datetime | None = None) -> bool:
        found = self.info(instrument, now)
        return found is not None and found.expired

    def expired_by(self, day: date) -> Callable[[Instrument], bool]:
        """A test: did this contract end before `day` began? (For the new trading day.)"""
        return lambda i: i.expiry is not None and i.expiry < day

    def settlement_price(
        self, instrument: Instrument, last: Callable[[str], int | None]
    ) -> int | None:
        """What one unit is worth at the end, in paise (see the module notes); None if no
        price is known to work it out from."""
        if instrument.kind not in OPTIONS:
            return last(instrument.id)
        under = self._underlying_price(instrument, last)
        if under is None or instrument.strike_paise is None:
            return last(instrument.id)
        if instrument.kind is InstrumentKind.CALL:
            return max(under - instrument.strike_paise, 0)
        return max(instrument.strike_paise - under, 0)

    def _underlying_price(
        self, option: Instrument, last: Callable[[str], int | None]
    ) -> int | None:
        """The same-expiry future (it ends at the underlying's price), else the share."""
        for future in self._store.derivatives(option.underlying or ""):
            if future.kind is InstrumentKind.FUTURE and future.expiry == option.expiry:
                if (price := last(future.id)) is not None:
                    return price
        for exchange in ("NSE", "BSE"):
            if (price := last(f"{exchange}:{option.underlying}")) is not None:
                return price
        return None


def _note(instrument: Instrument, expires_at: datetime, physical: bool,
          cutoff: datetime | None, expired: bool, today: bool) -> str:  # fmt: skip
    when = f"{expires_at:%H:%M} on {expires_at:%d %b %Y}"
    if expired:
        return f"{instrument.symbol} expired at {when}: it can't be traded any more"
    how = (
        "settles by delivery of the shares: an open future, or an option that ends in the "
        "money, becomes a trade in the shares"
        if physical
        else "settles in cash at the final settlement price"
    )
    words = f"{'Expires today' if today else 'Expires'} at {when}; {how}"
    if cutoff is not None:
        words += f"; from {cutoff:%H:%M} that day only closing trades are allowed"
    return words


def expiry_to_dict(info: ExpiryInfo | None) -> dict[str, Any] | None:
    if info is None:
        return None
    return {
        "expires_at": info.expires_at.isoformat(),
        "settlement": info.settlement,
        "expires_today": info.expires_today,
        "expired": info.expired,
        "opening_cutoff": info.opening_cutoff.isoformat() if info.opening_cutoff else None,
        "opening_closed": info.opening_closed,
        "note": info.note,
    }
