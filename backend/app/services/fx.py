from typing import Dict
from sqlalchemy.orm import Session
import bisect
from datetime import date, datetime, timedelta
import httpx
import uuid
import logging

from ..schemas.asset import Currency
from ..models.price import FxRate, FxDailyRate
from ..cache import cache
from ..config import settings
from ..providers.yahoo import YahooFinanceProvider
from .log_redaction import redact_secrets

logger = logging.getLogger(__name__)

MAX_RATE_GAP_DAYS = 7


class FxService:
    """Service for foreign exchange rate management."""

    def __init__(self, db: Session):
        self.db = db
        self.client = httpx.AsyncClient(timeout=10.0)
        self._daily: Dict[str, tuple] = {}
        self._warned: set = set()
        self._yahoo: YahooFinanceProvider | None = None

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
            logger.warning("FALLBACK [FX %s→%s]: API failed (%s), trying DB cache", from_currency.value, to_currency.value, redact_secrets(e))
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

    def _daily_series(self, code: str):
        """Sorted (dates, rates) for one currency, loaded once per instance."""
        series = self._daily.get(code)
        if series is None:
            rows = (
                self.db.query(FxDailyRate.date, FxDailyRate.per_usd)
                .filter(FxDailyRate.currency == Currency(code))
                .order_by(FxDailyRate.date)
                .all()
            )
            series = ([r[0] for r in rows], [r[1] for r in rows])
            self._daily[code] = series
        return series

    def _per_usd_at(self, code: str, day: date):
        """Units of `code` per 1 USD on `day` (latest row within the gap), or None."""
        if code == Currency.USD.value:
            return 1.0
        dates, rates = self._daily_series(code)
        i = bisect.bisect_right(dates, day) - 1
        if i < 0 or dates[i] < day - timedelta(days=MAX_RATE_GAP_DAYS):
            return None
        rate = rates[i]
        if rate is None or rate <= 0:
            return None
        return rate

    async def get_rate_at(
        self,
        from_currency: Currency,
        to_currency: Currency,
        day: date,
        strict: bool = False,
    ) -> float:
        """Rate for converting from -> to on `day`, routed through USD.

        Uses the latest stored daily rate on or before `day` (max 7 days old).
        When missing: raise ValueError if strict, else fall back to the live rate.
        """
        f = Currency(from_currency).value
        t = Currency(to_currency).value
        if f == t:
            return 1.0

        per_usd_from = self._per_usd_at(f, day)
        per_usd_to = self._per_usd_at(t, day)
        if per_usd_from is not None and per_usd_to is not None:
            return per_usd_to / per_usd_from

        if strict:
            raise ValueError(f"No daily FX rate {f}->{t} on {day}")
        if (f, t) not in self._warned:
            self._warned.add((f, t))
            logger.warning("FX %s->%s on %s: no daily rate, using current rate", f, t, day)
        return await self.get_rate(Currency(f), Currency(t))

    async def convert_at(
        self,
        amount: float,
        from_currency: Currency,
        to_currency: Currency,
        day: date,
        strict: bool = False,
    ) -> float:
        """Convert amount using the rate in effect on `day`."""
        rate = await self.get_rate_at(from_currency, to_currency, day, strict=strict)
        return amount * rate

    async def _fetch_rate(self, from_currency: Currency, to_currency: Currency) -> float:
        """Live rate: Yahoo {CCY}=X first (same source as fx_daily_rates), then ExchangeRate-API."""
        try:
            return await self._fetch_yahoo_rate(from_currency, to_currency)
        except Exception as e:
            logger.warning(
                "FALLBACK [FX %s→%s]: Yahoo failed (%s), trying ExchangeRate-API",
                from_currency.value, to_currency.value, redact_secrets(e),
            )
        return await self._fetch_exchangerate_api_rate(from_currency, to_currency)

    async def _yahoo_per_usd(self, code: str) -> float:
        if code == Currency.USD.value:
            return 1.0
        if self._yahoo is None:
            self._yahoo = YahooFinanceProvider()
        return await self._yahoo.get_fx_quote(code)

    async def _fetch_yahoo_rate(self, from_currency: Currency, to_currency: Currency) -> float:
        """Rate from -> to from Yahoo live quotes (CCY per USD), crossed through USD like get_rate_at."""
        per_usd_from = await self._yahoo_per_usd(from_currency.value)
        per_usd_to = await self._yahoo_per_usd(to_currency.value)
        return per_usd_to / per_usd_from

    async def _fetch_exchangerate_api_rate(self, from_currency: Currency, to_currency: Currency) -> float:
        """Fetch rate from ExchangeRate-API (keyed v6, else keyless v4)."""
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
        """Close HTTP clients."""
        await self.client.aclose()
        if self._yahoo is not None:
            await self._yahoo.close()
