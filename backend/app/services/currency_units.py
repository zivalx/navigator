"""Yahoo quotes some venues in a minor unit (agorot, pence, cents).

This module maps those codes to the major currency and the factor that
converts a minor-unit price into major units.
"""

_EXACT = {
    "ILA": ("ILS", 0.01),
    "GBp": ("GBP", 0.01),
    "GBX": ("GBP", 0.01),
    "ZAc": ("ZAR", 0.01),
}
# Case-insensitive fallback; deliberately excludes "GBP" (a major unit).
_FALLBACK = {
    "GBX": ("GBP", 0.01),
    "ILA": ("ILS", 0.01),
    "ZAC": ("ZAR", 0.01),
}


def normalize_quote_currency(code: str | None) -> tuple[str, float]:
    """Return (major currency code, unit factor). Blank or None means USD."""
    code = (code or "").strip()
    if not code:
        return "USD", 1.0
    if code in _EXACT:
        return _EXACT[code]
    return _FALLBACK.get(code.upper(), (code.upper(), 1.0))
