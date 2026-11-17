"""Derive an asset's currency, exchange and region from Yahoo chart metadata."""
import logging
from dataclasses import dataclass
from typing import Dict, Optional

import httpx
from fastapi import HTTPException

from ..models.asset import Currency, MarketRegion
from ..providers.yahoo import YahooFinanceProvider

logger = logging.getLogger(__name__)

VENUE_REGION: Dict[str, MarketRegion] = {
    "TLV": MarketRegion.IL,
    **{v: MarketRegion.US for v in "NMS NYQ NGM NCM ASE PCX BTS".split()},
    **{v: MarketRegion.EU for v in
       "LSE GER PAR AMS MIL MCE EBS STO CPH HEL OSL BRU LIS VIE IOB".split()},
    **{v: MarketRegion.ASIA for v in "TYO HKG SHH SHZ KSC TAI SES NSI BSE".split()},
}


@dataclass(frozen=True)
class ListingIdentity:
    currency: Currency
    exchange: Optional[str]
    market_region: Optional[MarketRegion]  # None = unknown venue


class ListingUnavailable(Exception):
    """Yahoo could not be reached."""


class UnsupportedCurrency(Exception):
    def __init__(self, code: str, symbol: str):
        super().__init__(f"unsupported currency {code} for {symbol}")
        self.code = code
        self.symbol = symbol


class SymbolNotFound(Exception):
    """Yahoo has no data for the symbol."""


async def derive_listing(symbol: str) -> ListingIdentity:
    symbol = symbol.upper()
    provider = YahooFinanceProvider()
    try:
        meta = await provider.get_chart_meta(symbol)
    except (httpx.HTTPError, ValueError) as e:  # ValueError: non-JSON body
        logger.warning("listing lookup failed for %s: %s", symbol, type(e).__name__)
        raise ListingUnavailable() from e
    finally:
        await provider.close()

    if meta is None:
        raise SymbolNotFound()

    code = meta.get("currency")
    try:
        currency = Currency(code)
    except ValueError:
        raise UnsupportedCurrency(str(code), symbol)

    venue = meta.get("exchangeName")
    return ListingIdentity(
        currency=currency,
        exchange=meta.get("fullExchangeName") or venue,
        market_region=VENUE_REGION.get(venue),
    )


def listing_http_error(exc: Exception) -> HTTPException:
    """Map a derive_listing failure to its HTTP response."""
    if isinstance(exc, UnsupportedCurrency):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, SymbolNotFound):
        return HTTPException(status_code=404, detail="symbol not found")
    return HTTPException(status_code=503, detail="couldn't verify symbol, try again")
