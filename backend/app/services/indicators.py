import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ..cache import cache
from ..config import settings
from ..providers.alternative_me import AlternativeMeFearGreedProvider
from ..providers.cnn import CNNFearGreedProvider
from ..providers.yahoo import YahooFinanceProvider
from . import breadth

logger = logging.getLogger(__name__)


# Indicator registry (v1) — keys/labels/categories per
# docs/superpowers/specs/2026-07-23-market-indicators-design.md
REGISTRY: List[Dict] = [
    {"key": "fear_greed_stocks", "label": "Fear & Greed", "category": "sentiment",
     "source": "cnn", "kind": "cnn", "unit": "index"},
    {"key": "fear_greed_crypto", "label": "Crypto Fear & Greed", "category": "sentiment",
     "source": "alternative.me", "kind": "alt_me", "unit": "index"},
    {"key": "vix", "label": "VIX", "category": "volatility",
     "source": "yahoo", "kind": "yahoo", "symbol": "^VIX", "unit": "points"},
    {"key": "sp500", "label": "S&P 500", "category": "index",
     "source": "yahoo", "kind": "yahoo", "symbol": "^GSPC", "unit": "points"},
    {"key": "nasdaq", "label": "Nasdaq", "category": "index",
     "source": "yahoo", "kind": "yahoo", "symbol": "^IXIC", "unit": "points"},
    {"key": "dow", "label": "Dow Jones", "category": "index",
     "source": "yahoo", "kind": "yahoo", "symbol": "^DJI", "unit": "points"},
    {"key": "russell2000", "label": "Russell 2000", "category": "index",
     "source": "yahoo", "kind": "yahoo", "symbol": "^RUT", "unit": "points"},
    {"key": "stoxx50", "label": "Euro Stoxx 50", "category": "index",
     "source": "yahoo", "kind": "yahoo", "symbol": "^STOXX50E", "unit": "points"},
    {"key": "dax", "label": "DAX", "category": "index",
     "source": "yahoo", "kind": "yahoo", "symbol": "^GDAXI", "unit": "points"},
    {"key": "smi", "label": "SMI (Swiss)", "category": "index",
     "source": "yahoo", "kind": "yahoo", "symbol": "^SSMI", "unit": "points"},
    {"key": "nikkei", "label": "Nikkei 225", "category": "index",
     "source": "yahoo", "kind": "yahoo", "symbol": "^N225", "unit": "points"},
    {"key": "us10y", "label": "US 10Y Yield", "category": "rates",
     "source": "yahoo", "kind": "yahoo", "symbol": "^TNX", "unit": "%", "scale": 0.1},
    {"key": "us30y", "label": "US 30Y Yield", "category": "rates",
     "source": "yahoo", "kind": "yahoo", "symbol": "^TYX", "unit": "%", "scale": 0.1},
    {"key": "s5fi", "label": "S&P 500 % Above 50-Day MA", "category": "breadth",
     "source": "computed", "kind": "breadth", "breadth_key": "s5fi", "unit": "%"},
    {"key": "s5th", "label": "S&P 500 % Above 200-Day MA", "category": "breadth",
     "source": "computed", "kind": "breadth", "breadth_key": "s5th", "unit": "%"},
    {"key": "dxy", "label": "Dollar Index", "category": "fx",
     "source": "yahoo", "kind": "yahoo", "symbol": "DX-Y.NYB", "unit": "points"},
    {"key": "gold", "label": "Gold", "category": "commodities",
     "source": "yahoo", "kind": "yahoo", "symbol": "GC=F", "unit": "USD"},
    {"key": "oil_wti", "label": "Crude Oil (WTI)", "category": "commodities",
     "source": "yahoo", "kind": "yahoo", "symbol": "CL=F", "unit": "USD"},
    {"key": "btc", "label": "Bitcoin", "category": "crypto",
     "source": "yahoo", "kind": "yahoo", "symbol": "BTC-USD", "unit": "USD"},
]

REGISTRY_BY_KEY: Dict[str, Dict] = {entry["key"]: entry for entry in REGISTRY}


class IndicatorsService:
    """Serves the market-indicators registry: sentiment gauges, VIX, indices,
    rates, fx, commodities, crypto, and computed breadth (S5FI/S5TH).

    Per-indicator failures never raise — a failed indicator is returned with
    value=None and error set, so the endpoint always responds 200.
    """

    def __init__(self):
        self.cnn = CNNFearGreedProvider()
        self.alt_me = AlternativeMeFearGreedProvider()
        self.yahoo = YahooFinanceProvider()

    async def close(self):
        await self.cnn.close()
        await self.alt_me.close()
        await self.yahoo.close()

    async def get_indicators(self, keys: Optional[List[str]] = None) -> Dict:
        """Build the indicators response for the given keys (or all, if
        keys is empty/None). Unknown keys are silently ignored.
        """
        if keys:
            entries = [REGISTRY_BY_KEY[k] for k in keys if k in REGISTRY_BY_KEY]
        else:
            entries = REGISTRY

        indicators = [await self._get_one(entry) for entry in entries]

        return {
            "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "indicators": indicators,
        }

    async def _get_one(self, entry: Dict) -> Dict:
        result = {
            "key": entry["key"],
            "label": entry["label"],
            "category": entry["category"],
            "value": None,
            "unit": entry.get("unit"),
            "rating": None,
            "change": None,
            "change_pct": None,
            "source": entry["source"],
            "error": None,
        }

        try:
            if entry["kind"] == "cnn":
                data = await self._fetch_sentiment_cached(
                    f"indicators:{entry['key']}", self.cnn.get_fear_greed
                )
                result["value"] = data.get("value")
                result["rating"] = data.get("rating")
                result["change"] = data.get("change")

            elif entry["kind"] == "alt_me":
                data = await self._fetch_sentiment_cached(
                    f"indicators:{entry['key']}", self.alt_me.get_fear_greed
                )
                result["value"] = data.get("value")
                result["rating"] = data.get("rating")
                result["change"] = data.get("change")

            elif entry["kind"] == "yahoo":
                result.update(await self._get_yahoo(entry))

            elif entry["kind"] == "breadth":
                result.update(await self._get_breadth(entry))

        except Exception as e:
            logger.warning("Indicator %s failed: %s", entry["key"], e)
            result["value"] = None
            result["error"] = str(e)

        return result

    async def _fetch_sentiment_cached(self, cache_key: str, fetch_fn) -> Dict:
        """Redis-cache sentiment provider fetches (indicators_cache_ttl, default 900s)."""
        cached = await cache.get(cache_key)
        if cached is not None:
            return cached
        data = await fetch_fn()
        await cache.set(cache_key, data, ttl=settings.indicators_cache_ttl)
        return data

    async def _get_yahoo(self, entry: Dict) -> Dict:
        """Yahoo-backed indicators ride the existing quote cache (60s)."""
        quote = await self.yahoo.get_quote(entry["symbol"])
        scale = entry.get("scale", 1)

        price = quote.get("price")
        change = quote.get("change")

        value = round(price * scale, 4) if price is not None else None
        change = round(change * scale, 4) if change is not None else None

        return {
            "value": value,
            "change": change,
            # change_pct is scale-invariant (ratio of change to previous close)
            "change_pct": quote.get("changePercent"),
        }

    async def _get_breadth(self, entry: Dict) -> Dict:
        """Computed breadth indicators (S5FI/S5TH): read-only from cache,
        never computed inline (computation takes minutes and runs only in
        the background scheduler).
        """
        cached = await breadth.get_cached_breadth(entry["breadth_key"])
        if not cached or cached.get("value") is None:
            return {"error": "breadth not yet computed"}

        value = cached.get("value")
        previous = cached.get("previous")
        change = round(value - previous, 2) if previous is not None else None

        return {"value": value, "change": change}
