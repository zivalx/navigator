from typing import Dict, List, Optional
from datetime import datetime
from .base import BaseProvider


class YahooFinanceProvider(BaseProvider):
    """Yahoo Finance provider — free, no API key required."""

    QUERY_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
    }

    # Major US tickers to scan for movers (S&P 100 subset + popular names)
    MOVER_TICKERS = [
        "AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "META", "TSLA", "BRK-B",
        "UNH", "JNJ", "V", "XOM", "JPM", "PG", "MA", "HD", "CVX", "MRK",
        "ABBV", "LLY", "PEP", "KO", "COST", "AVGO", "WMT", "MCD", "CSCO",
        "TMO", "ACN", "ABT", "DHR", "NEE", "LIN", "TXN", "PM", "UPS",
        "AMD", "CRM", "NFLX", "INTC", "BA", "DIS", "NKE", "AMGN", "IBM",
        "GS", "CAT", "LOW", "SBUX", "PFE", "RTX", "ORCL", "QCOM", "AMAT",
        "DE", "BKNG", "ADP", "MDLZ", "GILD", "MMM", "SYK", "ADI", "PYPL",
        "ISRG", "REGN", "VRTX", "LRCX", "MU", "PANW", "SNPS", "CDNS",
        "MRVL", "KLAC", "CRWD", "SMCI", "ARM", "PLTR", "COIN", "SQ",
        "SHOP", "UBER", "ABNB", "RIVN", "LCID", "SOFI", "HOOD", "RBLX",
    ]

    def supports_symbol(self, symbol: str, asset_type: str = "stock") -> bool:
        """Yahoo supports stocks, ETFs, indices — everything except crypto (use CoinGecko)."""
        return asset_type in ("stock", "etf", "index", "fund")

    async def get_quote(self, symbol: str) -> Dict:
        """Get quote from Yahoo Finance."""
        url = self.QUERY_URL.format(symbol=symbol.upper())
        params = {"interval": "1d", "range": "1d"}

        response = await self.client.get(url, params=params, headers=self.HEADERS)
        response.raise_for_status()
        data = response.json()

        result = data.get("chart", {}).get("result")
        if not result:
            raise ValueError(f"No Yahoo data for {symbol}")

        meta = result[0].get("meta", {})
        price = meta.get("regularMarketPrice", 0)
        prev_close = meta.get("chartPreviousClose", meta.get("previousClose", 0))
        change = round(price - prev_close, 2) if prev_close else 0
        change_pct = round((change / prev_close) * 100, 2) if prev_close else 0

        return {
            "symbol": symbol.upper(),
            "price": price,
            "change": change,
            "changePercent": change_pct,
            "timestamp": datetime.now().isoformat(),
            "currency": meta.get("currency", "USD"),
            "source": "yahoo",
        }

    async def get_quotes(self, symbols: List[str]) -> Dict[str, Dict]:
        """Get quotes for multiple symbols."""
        results = {}
        for symbol in symbols:
            try:
                results[symbol.upper()] = await self.get_quote(symbol)
            except Exception:
                continue
        return results

    async def get_movers(self, direction: str = "gainers", limit: int = 10) -> List[Dict]:
        """
        Get top gainers or losers by fetching quotes for major tickers
        and sorting by % change. Real prices, real moves.
        """
        quotes = await self.get_quotes(self.MOVER_TICKERS)

        movers = list(quotes.values())
        reverse = direction == "gainers"
        movers.sort(key=lambda q: q.get("changePercent", 0), reverse=reverse)

        return movers[:limit]

    async def search(self, query: str, limit: int = 10) -> List[Dict]:
        """Search for assets by name or symbol."""
        params = {
            "q": query,
            "quotesCount": limit,
            "newsCount": 0,
            "listsCount": 0,
        }

        response = await self.client.get(
            self.SEARCH_URL, params=params, headers=self.HEADERS
        )
        response.raise_for_status()
        data = response.json()

        results = []
        for quote in data.get("quotes", []):
            qtype = quote.get("quoteType", "").upper()
            if qtype not in ("EQUITY", "ETF", "MUTUALFUND", "INDEX", "CRYPTOCURRENCY"):
                continue

            asset_type = {
                "EQUITY": "stock",
                "ETF": "etf",
                "MUTUALFUND": "fund",
                "INDEX": "index",
                "CRYPTOCURRENCY": "crypto",
            }.get(qtype, "stock")

            results.append({
                "symbol": quote.get("symbol", ""),
                "name": quote.get("shortname") or quote.get("longname", ""),
                "exchange": quote.get("exchange", ""),
                "asset_type": asset_type,
                "currency": quote.get("currency", "USD"),
            })

        return results
