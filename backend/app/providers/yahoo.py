from typing import Dict, List, Optional
from datetime import datetime, timezone

from .base import BaseProvider


def _normalize(code):
    # Imported lazily: app.services' package init imports the providers.
    from ..services.currency_units import normalize_quote_currency

    return normalize_quote_currency(code)


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

    # Tel Aviv large caps (TA-35 names) scanned for region=IL movers.
    # Yahoo quotes these in agorot (ILA); get_quote normalizes to ILS.
    IL_MOVER_NAMES = {
        "LUMI.TA": "Bank Leumi", "POLI.TA": "Bank Hapoalim",
        "DSCT.TA": "Israel Discount Bank", "MZTF.TA": "Mizrahi Tefahot",
        "FIBI.TA": "First International Bank", "TEVA.TA": "Teva",
        "NICE.TA": "NICE", "ESLT.TA": "Elbit Systems", "ICL.TA": "ICL Group",
        "BEZQ.TA": "Bezeq", "AZRG.TA": "Azrieli Group", "ALHE.TA": "Alony Hetz",
        "MLSR.TA": "Melisron", "BIG.TA": "BIG Shopping Centers",
        "AMOT.TA": "Amot Investments", "HARL.TA": "Harel Insurance",
        "PHOE.TA": "Phoenix Financial", "CLIS.TA": "Clal Insurance",
        "MGDL.TA": "Migdal Insurance", "DLEKG.TA": "Delek Group",
        "NWMD.TA": "NewMed Energy", "ENLT.TA": "Enlight Renewable",
        "ORA.TA": "Ormat", "TSEM.TA": "Tower Semiconductor", "NVMI.TA": "Nova",
        "CAMT.TA": "Camtek", "SPEN.TA": "Shapir Engineering",
        "OPCE.TA": "OPC Energy", "STRS.TA": "Strauss Group",
        "SAE.TA": "Shufersal", "ENOG.TA": "Energean", "NXSN.TA": "Next Vision",
    }

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
        currency, factor = _normalize(meta.get("currency"))
        price = meta.get("regularMarketPrice", 0)
        prev_close = meta.get("chartPreviousClose", meta.get("previousClose", 0))
        # Percentage is computed in the raw unit so it is unaffected by the factor.
        raw_change = round(price - prev_close, 2) if prev_close else 0
        change_pct = round((raw_change / prev_close) * 100, 2) if prev_close else 0
        if factor == 1.0:
            change = raw_change
        else:
            price = round(price * factor, 6)
            change = round(raw_change * factor, 6)

        rmt = meta.get("regularMarketTime")
        market_time = (
            datetime.fromtimestamp(rmt, tz=timezone.utc).isoformat()
            if isinstance(rmt, (int, float)) and rmt
            else None
        )

        return {
            "symbol": symbol.upper(),
            "price": price,
            "change": change,
            "changePercent": change_pct,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "marketTime": market_time,
            "exchangeTimezone": meta.get("exchangeTimezoneName"),
            "currency": currency,
            "unitFactor": factor,
            "exchange": meta.get("fullExchangeName", meta.get("exchangeName", "")),
            "source": "yahoo",
        }

    async def get_chart_meta(self, symbol: str) -> Optional[Dict]:
        """Chart metadata (normalized currency, venue). None if Yahoo has no data.

        Raises httpx.HTTPError on network failure.
        """
        url = self.QUERY_URL.format(symbol=symbol.upper())
        response = await self.client.get(
            url, params={"interval": "1d", "range": "1d"}, headers=self.HEADERS
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        result = response.json().get("chart", {}).get("result")
        if not result:
            return None
        meta = result[0].get("meta", {})
        currency, factor = _normalize(meta.get("currency"))
        return {
            "currency": currency,
            "unitFactor": factor,
            "exchangeName": meta.get("exchangeName"),
            "fullExchangeName": meta.get("fullExchangeName"),
            "instrumentType": meta.get("instrumentType"),
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

    async def get_historical_changes(self, symbol: str) -> Dict:
        """
        Get price change % over multiple periods: 1d, 1mo, 6mo.
        Uses Yahoo chart endpoint with 6mo range, interval=1d.
        """
        url = self.QUERY_URL.format(symbol=symbol.upper())
        params = {"interval": "1d", "range": "6mo"}

        response = await self.client.get(url, params=params, headers=self.HEADERS)
        response.raise_for_status()
        data = response.json()

        result = data.get("chart", {}).get("result")
        if not result:
            return {}

        meta = result[0].get("meta", {})
        closes = result[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
        # Filter out None values but keep index alignment
        valid_closes = [c for c in closes if c is not None]

        if not valid_closes:
            return {}

        current = meta.get("regularMarketPrice", valid_closes[-1])

        changes = {}

        # 1d change — use second-to-last close as yesterday's close
        if len(valid_closes) >= 2:
            prev = valid_closes[-2]
            if prev and prev > 0:
                changes["change_1d"] = round((current - prev) / prev * 100, 2)

        # 1mo change (~21 trading days back)
        if len(valid_closes) >= 21:
            price_1m = valid_closes[-21]
            if price_1m and price_1m > 0:
                changes["change_1m"] = round((current - price_1m) / price_1m * 100, 2)

        # 6mo change (first close in range)
        price_6m = valid_closes[0]
        if price_6m and price_6m > 0:
            changes["change_6m"] = round((current - price_6m) / price_6m * 100, 2)

        return changes

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

    async def get_il_movers(self, limit: int = 10) -> Dict[str, List[Dict]]:
        """Tel Aviv gainers and losers from one pass over IL_MOVER_NAMES.

        Prices are ILS major units (agorot normalized by get_quote). Only
        names that actually moved are listed on each side.
        """
        quotes = await self.get_quotes(list(self.IL_MOVER_NAMES))
        movers = []
        for symbol, quote in quotes.items():
            movers.append({**quote, "name": self.IL_MOVER_NAMES.get(symbol, symbol)})
        movers.sort(key=lambda q: q.get("changePercent") or 0, reverse=True)
        gainers = [q for q in movers if (q.get("changePercent") or 0) > 0]
        losers = [q for q in reversed(movers) if (q.get("changePercent") or 0) < 0]
        return {"gainers": gainers[:limit], "losers": losers[:limit]}

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
                # Yahoo's display name for the venue ("NYSE", "Tel Aviv").
                "exchange_name": quote.get("exchDisp"),
                "asset_type": asset_type,
                # Search hits carry no currency; never invent one (the
                # quote / asset record is the source of truth).
                "currency": quote.get("currency"),
            })

        return results
