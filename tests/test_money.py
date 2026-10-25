"""Money as whole paise (domain/money.py): rupees to paise and back, bad amounts refused."""

from decimal import Decimal

import pytest

from laddrix.domain.money import to_paise, to_rupees


def test_rupees_to_paise():
    assert to_paise("2501.05") == 250105


def test_paise_back_to_rupees():
    assert to_rupees(250105) == Decimal("2501.05")


@pytest.mark.parametrize("bad", ["2501.055", "abc", "", "NaN", "Infinity"])
def test_bad_amounts_are_rejected(bad):
    with pytest.raises(ValueError):
        to_paise(bad)
