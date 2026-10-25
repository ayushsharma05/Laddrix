"""Instruments (domain/instruments.py): ids, the tick grid, whole lots, indices not tradable,
rules refused.
"""

import pytest

from laddrix.domain.instruments import Exchange, Instrument, InstrumentKind, make_instrument_id


def test_instrument_id_is_exchange_and_symbol(reliance, nifty_fut):
    assert reliance.id == "NSE:RELIANCE"
    assert nifty_fut.id == make_instrument_id(Exchange.NFO, "NIFTY26OCTFUT")


def test_price_on_the_tick_grid_is_accepted(reliance):
    reliance.check_price(250110)


@pytest.mark.parametrize("bad_price", [250105, 0, -10])
def test_price_off_grid_or_not_positive_is_rejected(reliance, bad_price):
    with pytest.raises(ValueError):
        reliance.check_price(bad_price)


def test_whole_lots_are_accepted(nifty_fut):
    nifty_fut.check_quantity(150)


@pytest.mark.parametrize("bad_qty", [100, 0, -75])
def test_partial_lots_or_non_positive_quantity_are_rejected(nifty_fut, bad_qty):
    with pytest.raises(ValueError):
        nifty_fut.check_quantity(bad_qty)


def test_indices_are_not_tradable(nifty_index, reliance):
    assert not nifty_index.tradable
    assert reliance.tradable


def test_instruments_cannot_be_changed(reliance):
    with pytest.raises(AttributeError):
        reliance.tick_paise = 5


@pytest.mark.parametrize("tick, lot", [(0, 1), (5, 0)])
def test_nonsense_rules_are_refused(tick, lot):
    with pytest.raises(ValueError):
        Instrument(Exchange.NSE, "X", InstrumentKind.EQUITY, tick_paise=tick, lot_size=lot)
