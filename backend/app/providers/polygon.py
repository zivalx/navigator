from typing import Dict, List
from datetime import datetime
import httpx
from .base import BaseProvider


class PolygonProvider(BaseProvider):
    """Polygon.io market data provider."""

    BASE_URL = "https://api.polygon.io"

    def supports_symbol(self, symbol: str, asset_type: str = "stock") -> bool:
        """Polygon supports stocks and some crypto."""
        return asset_type in ["stock", "etf", "crypto"]

    async def get_quote(self, symbol: str) -> Dict:
        """Get real-time quote from Polygon."""
        if not self.api_key:
            raise ValueError("Polygon API key not configured")

        try:
            # Try stock/equity endpoint first
            url = f"{self.BASE_URL}/v2/aggs/ticker/{symbol}/prev"
            params = {"apiKey": self.api_key}

            response = await self.client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            if data.get("status") == "OK" and data.get("results"):
                result = data["results"][0]
                # Calculate change from open to close
                close = result.get("c", 0)
                open_price = result.get("o", close)
                change = close - open_price
                change_percent = (change / open_price * 100) if open_price else 0

                return {
                    "symbol": symbol,
                    "price": close,
                    "change": round(change, 2),
                    "changePercent": round(change_percent, 2),
                    "timestamp": datetime.fromtimestamp(result.get("t", 0) / 1000),
                    "currency": "USD",
                    "volume": result.get("v"),
                    "high": result.get("h"),
                    "low": result.get("l"),
                    "open": result.get("o"),
                }
            else:
                raise ValueError(f"No data for symbol {symbol}")

        except httpx.HTTPError as e:
            raise ValueError(f"Polygon API error: {str(e)}")

    async def get_quotes(self, symbols: List[str]) -> Dict[str, Dict]:
        """Get quotes for multiple symbols."""
        results = {}
        for symbol in symbols:
            try:
                results[symbol] = await self.get_quote(symbol)
            except Exception:
                # Skip failed symbols
                continue
        return results

    async def get_snapshot(self, symbol: str) -> Dict:
        """Get detailed snapshot including bid/ask."""
        if not self.api_key:
            raise ValueError("Polygon API key not configured")

        url = f"{self.BASE_URL}/v2/snapshot/locale/us/markets/stocks/tickers/{symbol}"
        params = {"apiKey": self.api_key}

        response = await self.client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        if data.get("status") == "OK" and data.get("ticker"):
            ticker = data["ticker"]
            day = ticker.get("day", {})
            prev_day = ticker.get("prevDay", {})

            return {
                "symbol": symbol,
                "price": day.get("c", 0),
                "change": day.get("c", 0) - prev_day.get("c", 0),
                "changePercent": ((day.get("c", 0) - prev_day.get("c", 0)) / prev_day.get("c", 1)) * 100,
                "timestamp": datetime.now(),
                "currency": "USD",
                "volume": day.get("v", 0),
                "high": day.get("h", 0),
                "low": day.get("l", 0),
                "open": day.get("o", 0),
            }

        raise ValueError(f"No snapshot data for {symbol}")

    async def get_movers(self, direction: str = "gainers", limit: int = 10) -> List[Dict]:
        """Get top gainers or losers from Polygon snapshot."""
        if not self.api_key:
            raise ValueError("Polygon API key not configured")

        url = f"{self.BASE_URL}/v2/snapshot/locale/us/markets/stocks/{direction}"
        params = {"apiKey": self.api_key}

        response = await self.client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        results = []
        for ticker in data.get("tickers", [])[:limit]:
            day = ticker.get("day", {})
            prev = ticker.get("prevDay", {})
            price = day.get("c", 0)
            prev_close = prev.get("c", 0)
            change = price - prev_close if prev_close else 0
            change_pct = (change / prev_close * 100) if prev_close else 0
            results.append({
                "symbol": ticker.get("ticker", ""),
                "name": "",
                "price": round(price, 2),
                "change": round(change, 2),
                "changePercent": round(change_pct, 2),
                "volume": day.get("v", 0),
                "source": "polygon",
            })
        return results
