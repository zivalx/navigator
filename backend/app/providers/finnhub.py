from typing import Dict, List
from datetime import datetime
from .base import BaseProvider


class FinnhubProvider(BaseProvider):
    """Finnhub market data provider (fallback)."""

    BASE_URL = "https://finnhub.io/api/v1"

    def supports_symbol(self, symbol: str, asset_type: str = "stock") -> bool:
        """Finnhub supports stocks, forex, crypto."""
        return asset_type in ["stock", "etf", "crypto", "forex"]

    async def get_quote(self, symbol: str) -> Dict:
        """Get quote from Finnhub."""
        if not self.api_key:
            raise ValueError("Finnhub API key not configured")

        try:
            url = f"{self.BASE_URL}/quote"
            params = {
                "symbol": symbol,
                "token": self.api_key
            }

            response = await self.client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            if data.get("c"):  # Current price
                current_price = data["c"]
                change = data.get("d", 0)  # Change
                change_percent = data.get("dp", 0)  # Change percent

                return {
                    "symbol": symbol,
                    "price": current_price,
                    "change": round(change, 2),
                    "changePercent": round(change_percent, 2),
                    "timestamp": datetime.fromtimestamp(data.get("t", 0)),
                    "currency": "USD",
                    "high": data.get("h", 0),
                    "low": data.get("l", 0),
                    "open": data.get("o", 0),
                    "previousClose": data.get("pc", 0),
                }
            else:
                raise ValueError(f"No data for symbol {symbol}")

        except Exception as e:
            raise ValueError(f"Finnhub API error: {str(e)}")

    async def get_quotes(self, symbols: List[str]) -> Dict[str, Dict]:
        """Get quotes for multiple symbols."""
        results = {}
        for symbol in symbols:
            try:
                results[symbol] = await self.get_quote(symbol)
            except Exception:
                continue
        return results
