import pytest

from app.services.currency_units import normalize_quote_currency


@pytest.mark.parametrize("code,expected", [
    ("ILA", ("ILS", 0.01)), ("GBp", ("GBP", 0.01)), ("GBX", ("GBP", 0.01)),
    ("gbx", ("GBP", 0.01)), ("ZAc", ("ZAR", 0.01)), ("zac", ("ZAR", 0.01)),
    ("GBP", ("GBP", 1.0)), ("usd", ("USD", 1.0)), ("ILS", ("ILS", 1.0)),
    (None, ("USD", 1.0)), ("  ", ("USD", 1.0)),
])
def test_normalize_quote_currency(code, expected):
    assert normalize_quote_currency(code) == expected
