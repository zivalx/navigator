from typing import Dict, List, Optional
from sqlalchemy.orm import Session
import uuid
import logging
from datetime import datetime

from ..providers import PolygonProvider, FinnhubProvider, CoinGeckoProvider, YahooFinanceProvider, AlphaVantageProvider
from ..cache import cache
from ..config import settings
from ..models.asset import Asset
from ..models.price import PriceSnapshot

logger = logging.getLogger(__name__)


class MarketDataService:
    """Service for fetching market data with provider fallback and caching."""

    def __init__(self, db: Session):
        self.db = db
        self.yahoo = YahooFinanceProvider()
        self.coingecko = CoinGeckoProvider()
        self.polygon = PolygonProvider(api_key=settings.polygon_api_key) if settings.polygon_api_key else None
        self.finnhub = FinnhubProvider(api_key=settings.finnhub_api_key) if settings.finnhub_api_key else None
        self.alphavantage = AlphaVantageProvider(api_key=settings.alpha_vantage_api_key) if settings.alpha_vantage_api_key else None

    async def get_quote(self, symbol: str, asset_type: Optional[str] = None) -> Dict:
        """
        Get quote with caching and provider fallback.

        Priority for stocks/ETFs:
        1. Check cache
        2. Try Yahoo Finance (free, no key needed — default)
        3. Try Polygon (if API key configured)
        4. Try Finnhub (if API key configured)
        5. Return last known price from DB

        Priority for crypto:
        1. Check cache
        2. Try CoinGecko (free, no key needed)
        3. Return last known price from DB
        """
        cache_key = f"quote:{symbol.upper()}"

        # Try cache first
        cached = await cache.get(cache_key)
        if cached:
            return cached

        quote = None
        errors = []
        provider_used = None

        # Always look up asset type from DB for known assets
        asset = self.db.query(Asset).filter(Asset.symbol == symbol.upper()).first()
        if asset and asset.asset_type:
            asset_type = asset.asset_type.value if hasattr(asset.asset_type, 'value') else asset.asset_type
        elif not asset_type:
            asset_type = "stock"

        # Try crypto provider for crypto assets
        if asset_type == "crypto":
            try:
                quote = await self.coingecko.get_quote(symbol)
                provider_used = "coingecko"
            except Exception as e:
                errors.append(f"CoinGecko: {str(e)}")

        # For stocks/ETFs: Yahoo first (free, always available)
        if not quote and asset_type in ["stock", "etf"]:
            try:
                quote = await self.yahoo.get_quote(symbol)
                provider_used = "yahoo"
            except Exception as e:
                errors.append(f"Yahoo: {str(e)}")

        # Try Polygon if configured (higher rate limits / more data)
        if not quote and asset_type in ["stock", "etf"] and self.polygon:
            try:
                quote = await self.polygon.get_quote(symbol)
                provider_used = "polygon"
                logger.warning("FALLBACK [%s]: Yahoo failed, using Polygon. Errors: %s", symbol, "; ".join(errors))
            except Exception as e:
                errors.append(f"Polygon: {str(e)}")

        # Try Finnhub as last resort
        if not quote and self.finnhub:
            try:
                quote = await self.finnhub.get_quote(symbol)
                provider_used = "finnhub"
                logger.warning("FALLBACK [%s]: upstream providers failed, using Finnhub. Errors: %s", symbol, "; ".join(errors))
            except Exception as e:
                errors.append(f"Finnhub: {str(e)}")

        if quote:
            quote["source"] = provider_used
            # Cache the quote
            await cache.set(cache_key, quote, ttl=settings.quote_cache_ttl)

            # Store in database
            await self._store_price_snapshot(symbol, quote)

            return quote
        else:
            # All providers failed - try to return last known price from DB.
            # PriceSnapshot.asset_id is the Asset UUID, not the ticker, so
            # resolve through the asset row (already looked up above).
            last_price = None
            if asset:
                last_price = self.db.query(PriceSnapshot).filter(
                    PriceSnapshot.asset_id == asset.id
                ).order_by(PriceSnapshot.timestamp.desc()).first()

            if last_price:
                logger.warning("FALLBACK [%s]: all providers failed, returning DB cached price. Errors: %s", symbol, "; ".join(errors))
                return {
                    "symbol": symbol,
                    "price": last_price.price,
                    "change": 0,
                    "changePercent": 0,
                    "timestamp": last_price.timestamp,
                    "currency": last_price.currency.value,
                    "source": "db_cache",
                }

            raise ValueError(f"Could not fetch quote for {symbol}. Errors: {'; '.join(errors)}")

    async def get_quotes(self, symbols: List[str]) -> Dict[str, Dict]:
        """Get quotes for multiple symbols."""
        results = {}
        for symbol in symbols:
            try:
                results[symbol] = await self.get_quote(symbol)
            except Exception as e:
                results[symbol] = {"error": str(e)}
        return results

    async def _store_price_snapshot(self, symbol: str, quote: Dict):
        """Store price snapshot in database."""
        try:
            # Find asset by symbol
            asset = self.db.query(Asset).filter(Asset.symbol == symbol.upper()).first()
            if not asset:
                return

            snapshot = PriceSnapshot(
                id=str(uuid.uuid4()),
                asset_id=asset.id,
                price=quote["price"],
                currency=quote.get("currency", "USD"),
                timestamp=quote.get("timestamp", datetime.now()),
                source=quote.get("source", "polygon"),
            )
            self.db.add(snapshot)
            self.db.commit()
        except Exception:
            # Don't fail if we can't store - just skip
            self.db.rollback()

    async def _fetch_and_cache_movers(self) -> None:
        """
        Fetch both gainers and losers in a single API call and cache them.
        Alpha Vantage (1 call for both) → Polygon → Yahoo (curated fallback).
        """
        # Try Alpha Vantage — one call gets both gainers + losers
        if self.alphavantage:
            try:
                both = await self.alphavantage.get_all_movers()
                if both.get("gainers") or both.get("losers"):
                    await cache.set("movers:gainers", both["gainers"], ttl=1800)
                    await cache.set("movers:losers", both["losers"], ttl=1800)
                    return
            except Exception as e:
                logger.warning("FALLBACK [movers]: Alpha Vantage failed (%s)", e)

        # Try Polygon for each direction
        if self.polygon:
            try:
                for direction in ("gainers", "losers"):
                    result = await self.polygon.get_movers(direction, 20)
                    if result:
                        await cache.set(f"movers:{direction}", result, ttl=1800)
                return
            except Exception as e:
                logger.warning("FALLBACK [movers]: Polygon failed (%s)", e)

        # Yahoo curated list as last resort
        try:
            for direction in ("gainers", "losers"):
                result = await self.yahoo.get_movers(direction, 20)
                if result:
                    await cache.set(f"movers:{direction}", result, ttl=1800)
            logger.warning("FALLBACK [movers]: using Yahoo curated list")
            return
        except Exception as e:
            logger.warning("FALLBACK [movers]: Yahoo also failed (%s)", e)

        logger.warning("ALL PROVIDERS FAILED [movers]: no data available")

    async def _get_movers(self, direction: str, limit: int) -> List[Dict]:
        """Get movers from cache, fetching if needed."""
        cache_key = f"movers:{direction}"
        cached = await cache.get(cache_key)
        if cached:
            return cached[:limit]

        await self._fetch_and_cache_movers()

        cached = await cache.get(cache_key)
        return cached[:limit] if cached else []

    async def get_top_gainers(self, limit: int = 10, region: str = "US") -> List[Dict]:
        """Get top gaining stocks from live providers."""
        return await self._get_movers("gainers", limit)

    async def get_top_losers(self, limit: int = 10, region: str = "US") -> List[Dict]:
        """Get top losing stocks from live providers."""
        return await self._get_movers("losers", limit)

    async def get_historical_changes(self, symbol: str) -> Dict:
        """Get 1d, 1mo, 6mo change % for a symbol via Yahoo."""
        cache_key = f"hist:{symbol.upper()}"
        cached = await cache.get(cache_key)
        if cached:
            return cached
        try:
            changes = await self.yahoo.get_historical_changes(symbol)
            if changes:
                await cache.set(cache_key, changes, ttl=1800)
            return changes
        except Exception:
            return {}

    async def search_assets(self, query: str, limit: int = 10) -> List[Dict]:
        """Search for assets by name or ticker symbol."""
        try:
            return await self.yahoo.search(query, limit=limit)
        except Exception:
            return []

    async def close(self):
        """Close all provider connections."""
        await self.yahoo.close()
        await self.coingecko.close()
        if self.polygon:
            await self.polygon.close()
        if self.finnhub:
            await self.finnhub.close()
        if self.alphavantage:
            await self.alphavantage.close()
