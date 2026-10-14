"""The contracts the market simulator prices, with their starting prices (paise).

Used by the demo and by the server in simulated-price mode (paper trading without Kite).
"""

from datetime import date

from mocktrade.domain.instruments import Exchange, Instrument, InstrumentKind

EXPIRY = date(2026, 10, 27)
K = InstrumentKind


INSTRUMENTS: list[tuple[Instrument, int]] = [
    (Instrument(Exchange.NFO, "NIFTY26OCTFUT", K.FUTURE, 10, 75, EXPIRY, None, "NIFTY"), 2501000),
    (
        Instrument(
            Exchange.NFO, "NIFTY26NOVFUT", K.FUTURE, 10, 75, date(2026, 11, 24), None, "NIFTY"
        ),
        2514860,
    ),
    (
        Instrument(Exchange.NFO, "BANKNIFTY26OCTFUT", K.FUTURE, 20, 35, EXPIRY, None, "BANKNIFTY"),
        5621460,
    ),
    (Instrument(Exchange.NFO, "NIFTY26OCT25000CE", K.CALL, 5, 75, EXPIRY, 2500000, "NIFTY"), 12005),
    (Instrument(Exchange.NFO, "NIFTY26OCT25000PE", K.PUT, 5, 75, EXPIRY, 2500000, "NIFTY"), 9540),
    (Instrument(Exchange.NSE, "RELIANCE", K.EQUITY, 10, 1), 140020),
    (Instrument(Exchange.NSE, "HDFCBANK", K.EQUITY, 10, 1), 174280),
    (Instrument(Exchange.NSE, "INFY", K.EQUITY, 10, 1), 150340),
    (Instrument(Exchange.NSE, "SBIN", K.EQUITY, 5, 1), 87325),
    (Instrument(Exchange.NSE, "ICICIBANK", K.EQUITY, 10, 1), 128600),
]
