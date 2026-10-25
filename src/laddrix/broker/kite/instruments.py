"""Load Kite's daily instrument dump (CSV) into the instrument master.

Kite publishes one CSV of all instruments each day (fetch it around 08:30).
Columns used: instrument_token, tradingsymbol, name, expiry, strike, tick_size,
lot_size, instrument_type, segment, exchange.
"""

import csv
import io
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from laddrix.domain.instruments import Exchange, Instrument, InstrumentKind
from laddrix.domain.money import to_paise
from laddrix.refdata.store import InstrumentStore

log = logging.getLogger(__name__)

BROKER = "kite"
_KINDS = {
    "EQ": InstrumentKind.EQUITY,
    "FUT": InstrumentKind.FUTURE,
    "CE": InstrumentKind.CALL,
    "PE": InstrumentKind.PUT,
}
_EXCHANGES = {e.value: e for e in Exchange}


@dataclass(frozen=True)
class LoadReport:
    loaded: int
    skipped: int  # rows for exchanges or segments Laddrix doesn't trade
    bad: int  # rows that couldn't be read


def load_kite_instruments(csv_text: str, store: InstrumentStore) -> LoadReport:
    """Parse the dump, add instruments to `store` and map Kite tokens to our ids."""
    loaded = skipped = bad = 0
    for row in csv.DictReader(io.StringIO(csv_text)):
        try:
            parsed = _parse_row(row)
        except (KeyError, ValueError) as exc:
            bad += 1
            log.warning("bad instrument row %s: %s", row.get("instrument_token"), exc)
            continue
        if parsed is None:
            skipped += 1
            continue
        instrument, token = parsed
        store.add(instrument)
        store.map_broker(BROKER, token, instrument.id)
        loaded += 1
    log.info("kite instruments: %d loaded, %d skipped, %d bad", loaded, skipped, bad)
    return LoadReport(loaded, skipped, bad)


def _parse_row(row: dict[str, str]) -> tuple[Instrument, str] | None:
    exchange = _EXCHANGES.get(row["exchange"])
    if exchange is None:
        return None  # currency, commodity and other segments
    if row["segment"] == "INDICES":
        kind = InstrumentKind.INDEX
    else:
        known = _KINDS.get(row["instrument_type"])
        if known is None:
            return None
        kind = known
    tick_paise = to_paise(row["tick_size"] or "0")
    lot_size = int(row["lot_size"] or "0")
    if kind is InstrumentKind.INDEX:  # indices have no tick or lot in the dump
        tick_paise, lot_size = max(tick_paise, 1), max(lot_size, 1)
    strike = Decimal(row["strike"] or "0")
    instrument = Instrument(
        exchange=exchange,
        symbol=row["tradingsymbol"],
        kind=kind,
        tick_paise=tick_paise,
        lot_size=lot_size,
        expiry=date.fromisoformat(row["expiry"]) if row["expiry"] else None,
        strike_paise=to_paise(strike)
        if kind in (InstrumentKind.CALL, InstrumentKind.PUT)
        else None,
        underlying=row["name"] or None,
    )
    return instrument, row["instrument_token"]
