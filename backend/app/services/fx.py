from typing import Dict
from sqlalchemy.orm import Session
from datetime import datetime
import httpx
import uuid
import logging

from ..schemas.asset import Currency
from ..models.price import FxRate
from ..cache import cache
from ..config import settings

logger = logging.getLogger(__name__)


class FxService:
    """Service for foreign exchange rate management."""

    def __init__(self, db: Session):
        self.db = db
        self.client = httpx.AsyncClient(timeout=10.0)

    async def get_rate(self, from_currency: Currency, to_currency: Currency) -> float:
        """Get FX rate with caching."""
        if from_currency == to_currency:
            return 1.0

        cache_key = f"fx:{from_currency.value}:{to_currency.value}"

        # Try cache first
        cached = await cache.get(cache_key)
        if cached:
            return cached

        # Try to fetch from API
        try:
            rate = await self._fetch_rate(from_currency, to_currency)
            await cache.set(cache_key, rate, ttl=settings.fx_cache_ttl)

            # Store in database
            await self._store_fx_rate(from_currency, to_currency, rate)

            return rate
        except Exception as e:
            logger.warning("FALLBACK [FX %s→%s]: API failed (%s), trying DB cache", from_currency.value, to_currency.value, e)
            # Fallback to database
            fx_rate = self.db.query(FxRate).filter(
                FxRate.base_currency == from_currency,
                FxRate.quote_currency == to_currency
            ).order_by(FxRate.timestamp.desc()).first()

            if fx_rate:
                return fx_rate.rate
            else:
                # Try reverse rate
                reverse = self.db.query(FxRate).filter(
                    FxRate.base_currency == to_currency,
                    FxRate.quote_currency == from_currency
                ).order_by(FxRate.timestamp.desc()).first()

                if reverse:
                    return 1.0 / reverse.rate

            raise ValueError(f"Could not get FX rate for {from_currency}/{to_currency}: {e}")

    async def convert(self, amount: float, from_currency: Currency, to_currency: Currency) -> float:
        """Convert amount from one currency to another."""
        if from_currency == to_currency:
            return amount

        rate = await self.get_rate(from_currency, to_currency)
        return amount * rate

    async def _fetch_rate(self, from_currency: Currency, to_currency: Currency) -> float:
        """Fetch rate from external API (ExchangeRate-API or similar)."""
        # Using ExchangeRate-API (free tier: 1500 requests/month)
        if settings.exchangerate_api_key:
            url = f"https://v6.exchangerate-api.com/v6/{settings.exchangerate_api_key}/pair/{from_currency.value}/{to_currency.value}"

            response = await self.client.get(url)
            response.raise_for_status()
            data = response.json()

            if data.get("result") == "success":
                return data["conversion_rate"]
            else:
                raise ValueError(data.get("error-type", "Unknown error"))
        else:
            # Fallback to free API (no key required, but limited)
            logger.warning("FALLBACK [FX]: no ExchangeRate API key, using free tier")
            url = f"https://api.exchangerate-api.com/v4/latest/{from_currency.value}"
            response = await self.client.get(url)
            response.raise_for_status()
            data = response.json()

            if "rates" in data and to_currency.value in data["rates"]:
                return data["rates"][to_currency.value]
            else:
                raise ValueError("Currency not found in rates")

    async def _store_fx_rate(self, from_currency: Currency, to_currency: Currency, rate: float):
        """Store FX rate in database."""
        try:
            fx_rate = FxRate(
                id=str(uuid.uuid4()),
                base_currency=from_currency,
                quote_currency=to_currency,
                rate=rate,
                timestamp=datetime.now(),
            )
            self.db.add(fx_rate)
            self.db.commit()
        except Exception:
            self.db.rollback()

    async def close(self):
        """Close HTTP client."""
        await self.client.aclose()
