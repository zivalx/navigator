"""Currency-aware money formatting for human-facing messages."""

_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "ILS": "₪", "JPY": "¥"}


def format_money(value: float | None, currency: str) -> str:
    """Format ``value`` with the currency's symbol, e.g. "₪65.12".

    Currencies without a known symbol render as "1,234.50 CHF"; None as "—".
    """
    if value is None:
        return "—"
    code = (currency or "").upper()
    symbol = _SYMBOLS.get(code)
    if symbol:
        return f"{symbol}{value:,.2f}"
    return f"{value:,.2f} {code}".rstrip()
