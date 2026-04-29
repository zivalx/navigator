from typing import Dict, List
from datetime import datetime
from .base import BaseProvider


class CoinGeckoProvider(BaseProvider):
    """CoinGecko crypto data provider (no API key needed for basic usage)."""

    BASE_URL = "https://api.coingecko.com/api/v3"

    # Map common crypto symbols to CoinGecko IDs
    SYMBOL_MAP = {
        "BTC": "bitcoin",
        "ETH": "ethereum",
        "USDT": "tether",
        "BNB": "binancecoin",
        "SOL": "solana",
        "XRP": "ripple",
        "ADA": "cardano",
        "DOGE": "dogecoin",
        "AVAX": "avalanche-2",
        "DOT": "polkadot",
        "MATIC": "matic-network",
        "LTC": "litecoin",
        "LINK": "chainlink",
        "UNI": "uniswap",
        "ATOM": "cosmos",
    }

    def supports_symbol(self, symbol: str, asset_type: str = "stock") -> bool:
        """CoinGecko only supports crypto."""
        return asset_type == "crypto" and symbol.upper() in self.SYMBOL_MAP

    def _get_coin_id(self, symbol: str) -> str:
        """Convert symbol to CoinGecko coin ID."""
        return self.SYMBOL_MAP.get(symbol.upper(), symbol.lower())

    async def get_quote(self, symbol: str) -> Dict:
        """Get crypto quote from CoinGecko."""
        try:
            coin_id = self._get_coin_id(symbol)
            url = f"{self.BASE_URL}/simple/price"
            params = {
                "ids": coin_id,
                "vs_currencies": "usd",
                "include_24hr_change": "true",
                "include_24hr_vol": "true",
            }

            response = await self.client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            if coin_id in data:
                coin_data = data[coin_id]
                price = coin_data.get("usd", 0)
                change_percent = coin_data.get("usd_24h_change", 0)
                change = price * (change_percent / 100)

                return {
                    "symbol": symbol.upper(),
                    "price": price,
                    "change": round(change, 2),
                    "changePercent": round(change_percent, 2),
                    "timestamp": datetime.now().isoformat(),
                    "currency": "USD",
                    "volume24h": coin_data.get("usd_24h_vol", 0),
                }
            else:
                raise ValueError(f"No data for {symbol}")

        except Exception as e:
            raise ValueError(f"CoinGecko API error: {str(e)}")

    async def get_quotes(self, symbols: List[str]) -> Dict[str, Dict]:
        """Get quotes for multiple crypto symbols."""
        try:
            coin_ids = [self._get_coin_id(s) for s in symbols]
            url = f"{self.BASE_URL}/simple/price"
            params = {
                "ids": ",".join(coin_ids),
                "vs_currencies": "usd",
                "include_24hr_change": "true",
                "include_24hr_vol": "true",
            }

            response = await self.client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            results = {}
            for symbol in symbols:
                coin_id = self._get_coin_id(symbol)
                if coin_id in data:
                    coin_data = data[coin_id]
                    price = coin_data.get("usd", 0)
                    change_percent = coin_data.get("usd_24h_change", 0)
                    change = price * (change_percent / 100)

                    results[symbol.upper()] = {
                        "symbol": symbol.upper(),
                        "price": price,
                        "change": round(change, 2),
                        "changePercent": round(change_percent, 2),
                        "timestamp": datetime.now().isoformat(),
                        "currency": "USD",
                        "volume24h": coin_data.get("usd_24h_vol", 0),
                    }

            return results

        except Exception:
            return {}
