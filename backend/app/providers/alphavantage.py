from typing import Dict, List
from .base import BaseProvider


class AlphaVantageProvider(BaseProvider):
    """Alpha Vantage provider — real market-wide data."""

    BASE_URL = "https://www.alphavantage.co/query"

    def supports_symbol(self, symbol: str, asset_type: str = "stock") -> bool:
        return asset_type in ("stock", "etf")

    async def get_quote(self, symbol: str) -> Dict:
        if not self.api_key:
            raise ValueError("Alpha Vantage API key not configured")

        params = {
            "function": "GLOBAL_QUOTE",
            "symbol": symbol,
            "apikey": self.api_key,
        }
        response = await self.client.get(self.BASE_URL, params=params)
        response.raise_for_status()
        data = response.json()

        gq = data.get("Global Quote", {})
        if not gq or not gq.get("05. price"):
            raise ValueError(f"No Alpha Vantage data for {symbol}")

        price = float(gq["05. price"])
        change = float(gq.get("09. change", 0))
        change_pct = float(gq.get("10. change percent", "0").rstrip("%"))

        return {
            "symbol": symbol.upper(),
            "price": price,
            "change": round(change, 2),
            "changePercent": round(change_pct, 2),
            "currency": "USD",
            "source": "alphavantage",
        }

    async def get_quotes(self, symbols: List[str]) -> Dict[str, Dict]:
        results = {}
        for symbol in symbols:
            try:
                results[symbol.upper()] = await self.get_quote(symbol)
            except Exception:
                continue
        return results

    async def get_all_movers(self) -> Dict[str, List[Dict]]:
        """
        Single API call returns both gainers and losers for the entire US market.
        """
        if not self.api_key:
            raise ValueError("Alpha Vantage API key not configured")

        params = {
            "function": "TOP_GAINERS_LOSERS",
            "apikey": self.api_key,
        }
        response = await self.client.get(self.BASE_URL, params=params)
        response.raise_for_status()
        data = response.json()

        result = {}
        for direction in ("gainers", "losers"):
            key = f"top_{direction}"
            items = data.get(key, [])
            parsed = []
            for item in items:
                price = float(item.get("price", 0))
                change = float(item.get("change_amount", 0))
                change_pct = float(item.get("change_percentage", "0").rstrip("%"))
                parsed.append({
                    "symbol": item.get("ticker", ""),
                    "name": "",
                    "price": round(price, 2),
                    "change": round(change, 2),
                    "changePercent": round(change_pct, 2),
                    "volume": int(item.get("volume", 0)),
                    "source": "alphavantage",
                })
            result[direction] = parsed
        return result
