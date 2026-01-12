from typing import Dict, List, Optional
from sqlalchemy.orm import Session
import uuid
from datetime import datetime

from ..providers import PolygonProvider, FinnhubProvider, CoinGeckoProvider
from ..cache import cache
from ..config import settings
from ..models.asset import Asset
from ..models.price import PriceSnapshot


class MarketDataService:
    """Service for fetching market data with provider fallback and caching."""

    def __init__(self, db: Session):
        self.db = db
        self.polygon = PolygonProvider(api_key=settings.polygon_api_key) if settings.polygon_api_key else None
        self.finnhub = FinnhubProvider(api_key=settings.finnhub_api_key) if settings.finnhub_api_key else None
        self.coingecko = CoinGeckoProvider()

    async def get_quote(self, symbol: str, asset_type: str = "stock") -> Dict:
        """
        Get quote with caching and provider fallback.

        Priority:
        1. Check cache
        2. Try Polygon (for stocks/ETFs)
        3. Try CoinGecko (for crypto)
        4. Try Finnhub (fallback for everything)
        5. Return cached value if all fail
        """
        cache_key = f"quote:{symbol.upper()}"

        # Try cache first
        cached = await cache.get(cache_key)
        if cached:
            return cached

        quote = None
        errors = []

        # Determine asset type if not provided
        if not asset_type:
            asset = self.db.query(Asset).filter(Asset.symbol == symbol.upper()).first()
            asset_type = asset.asset_type if asset else "stock"

        # Try crypto provider for crypto assets
        if asset_type == "crypto":
            try:
                quote = await self.coingecko.get_quote(symbol)
            except Exception as e:
                errors.append(f"CoinGecko: {str(e)}")

        # Try Polygon for stocks/ETFs
        if not quote and asset_type in ["stock", "etf"] and self.polygon:
            try:
                quote = await self.polygon.get_quote(symbol)
            except Exception as e:
                errors.append(f"Polygon: {str(e)}")

        # Try Finnhub as fallback
        if not quote and self.finnhub:
            try:
                quote = await self.finnhub.get_quote(symbol)
            except Exception as e:
                errors.append(f"Finnhub: {str(e)}")

        if quote:
            # Cache the quote
            await cache.set(cache_key, quote, ttl=settings.quote_cache_ttl)

            # Store in database
            await self._store_price_snapshot(symbol, quote)

            return quote
        else:
            # All providers failed - try to return last known price from DB
            last_price = self.db.query(PriceSnapshot).filter(
                PriceSnapshot.asset_id == symbol
            ).order_by(PriceSnapshot.timestamp.desc()).first()

            if last_price:
                return {
                    "symbol": symbol,
                    "price": last_price.price,
                    "change": 0,
                    "changePercent": 0,
                    "timestamp": last_price.timestamp,
                    "currency": last_price.currency.value,
                    "source": "cached",
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

    async def get_top_gainers(self, limit: int = 10, region: str = "US") -> List[Dict]:
        """Get top gaining stocks (mock implementation for now)."""
        # TODO: Implement with real provider data
        # For now, return mock data matching frontend
        return [
            {"symbol": "NVDA", "name": "NVIDIA", "price": 138.72, "change": 4.26},
            {"symbol": "AMD", "name": "AMD Inc.", "price": 142.50, "change": 3.82},
            {"symbol": "SMCI", "name": "Super Micro", "price": 589.20, "change": 3.15},
            {"symbol": "ARM", "name": "ARM Holdings", "price": 168.45, "change": 2.98},
            {"symbol": "MU", "name": "Micron", "price": 98.32, "change": 2.67},
        ]

    async def get_top_losers(self, limit: int = 10, region: str = "US") -> List[Dict]:
        """Get top losing stocks (mock implementation for now)."""
        # TODO: Implement with real provider data
        return [
            {"symbol": "BA", "name": "Boeing", "price": 178.32, "change": -4.52},
            {"symbol": "DIS", "name": "Disney", "price": 112.45, "change": -3.21},
            {"symbol": "NFLX", "name": "Netflix", "price": 485.20, "change": -2.87},
            {"symbol": "PFE", "name": "Pfizer", "price": 28.45, "change": -2.45},
            {"symbol": "WBA", "name": "Walgreens", "price": 12.34, "change": -2.12},
        ]

    async def close(self):
        """Close all provider connections."""
        if self.polygon:
            await self.polygon.close()
        if self.finnhub:
            await self.finnhub.close()
        await self.coingecko.close()
