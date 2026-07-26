"""
Market breadth computation (S5FI / S5TH) — % of S&P 500 constituents trading
above their 50-day / 200-day simple moving average.

There is no free API for these StockCharts-style breadth indices, so we
compute them ourselves from raw price history. This is slow (network- and
CPU-heavy — minutes, not seconds) and is intended to run ONLY from the
background scheduler (see app/tasks/scheduler.py). The /indicators endpoint
only ever reads the cached result (see get_cached_breadth).
"""

import asyncio
import io
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import httpx
import pandas as pd
import yfinance as yf

from ..cache import cache

logger = logging.getLogger(__name__)

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
WIKI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
}
FALLBACK_PATH = Path(__file__).resolve().parent.parent / "data" / "sp500_constituents.txt"

CHUNK_SIZE = 100

CACHE_KEYS = {
    "s5fi": "breadth:s5fi",
    "s5th": "breadth:s5th",
}


def _load_fallback_constituents() -> List[str]:
    """Load the bundled snapshot of S&P 500 tickers (one per line)."""
    if not FALLBACK_PATH.exists():
        return []
    with open(FALLBACK_PATH) as f:
        return [line.strip() for line in f if line.strip()]


async def fetch_sp500_constituents() -> List[str]:
    """
    Fetch current S&P 500 constituent tickers from Wikipedia's
    "List of S&P 500 companies" page (first table), converting dots to
    dashes for Yahoo compatibility (BRK.B -> BRK-B).

    Falls back to the bundled snapshot at app/data/sp500_constituents.txt
    if the live fetch fails for any reason (network, layout change, etc).
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                WIKI_URL, headers=WIKI_HEADERS, follow_redirects=True
            )
            response.raise_for_status()

        tables = pd.read_html(io.StringIO(response.text))
        df = tables[0]
        symbols = [
            str(s).strip().replace(".", "-")
            for s in df["Symbol"].tolist()
            if str(s).strip()
        ]
        if not symbols:
            raise ValueError("Wikipedia table returned no symbols")
        return symbols
    except Exception as e:
        logger.warning(
            "Breadth: Wikipedia constituent fetch failed (%s); using bundled fallback",
            e,
        )
        return _load_fallback_constituents()


def _chunk(items: List[str], size: int) -> List[List[str]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _download_chunk_closes(symbols: List[str]) -> pd.DataFrame:
    """
    Blocking call: batch-download ~1y of daily closes for a chunk of
    symbols via yf.download. Returns a DataFrame with one column per
    symbol (missing/failed symbols are simply absent).
    """
    data = yf.download(
        symbols,
        period="1y",
        interval="1d",
        group_by="ticker",
        progress=False,
        threads=True,
        auto_adjust=False,
    )

    closes: Dict[str, pd.Series] = {}
    if isinstance(data.columns, pd.MultiIndex):
        top_level = set(data.columns.get_level_values(0))
        for symbol in symbols:
            if symbol in top_level and "Close" in data[symbol]:
                series = data[symbol]["Close"].dropna()
                if not series.empty:
                    closes[symbol] = data[symbol]["Close"]
    elif len(symbols) == 1 and "Close" in data.columns:
        closes[symbols[0]] = data["Close"]

    return pd.DataFrame(closes)


def compute_breadth_from_closes(closes: pd.DataFrame) -> Dict[str, Dict[str, Optional[float]]]:
    """
    Compute % of constituents above their 50-day (s5fi) and 200-day (s5th)
    SMA, for the latest and previous trading day.

    `closes` must have one column per symbol and one row per trading day,
    in ascending chronological order. Symbols with insufficient history for
    a given window are naturally excluded from that window's percentage
    (their rolling SMA is NaN) rather than failing the whole computation.
    """
    if closes.empty or len(closes) < 2:
        raise ValueError("Not enough price history to compute breadth")

    sma50 = closes.rolling(window=50, min_periods=50).mean()
    sma200 = closes.rolling(window=200, min_periods=200).mean()

    def pct_above(price_row: pd.Series, sma_row: pd.Series) -> Optional[float]:
        valid = price_row.notna() & sma_row.notna()
        total = int(valid.sum())
        if total == 0:
            return None
        above = int((price_row[valid] > sma_row[valid]).sum())
        return round(above / total * 100, 2)

    latest_prices, prev_prices = closes.iloc[-1], closes.iloc[-2]
    latest_sma50, prev_sma50 = sma50.iloc[-1], sma50.iloc[-2]
    latest_sma200, prev_sma200 = sma200.iloc[-1], sma200.iloc[-2]

    return {
        "s5fi": {
            "value": pct_above(latest_prices, latest_sma50),
            "previous": pct_above(prev_prices, prev_sma50),
        },
        "s5th": {
            "value": pct_above(latest_prices, latest_sma200),
            "previous": pct_above(prev_prices, prev_sma200),
        },
    }


async def compute_and_store_breadth() -> None:
    """
    Full breadth pipeline: fetch constituents, batch-download ~1y of daily
    closes, compute S5FI/S5TH, and cache the results in Redis without
    expiry (overwritten on the next run). Intended to be called only from
    the background scheduler — never inline from a request.
    """
    try:
        symbols = await fetch_sp500_constituents()
        if not symbols:
            logger.warning("Breadth: no constituents available, skipping computation")
            return

        all_closes = []
        for chunk_symbols in _chunk(symbols, CHUNK_SIZE):
            try:
                chunk_df = await asyncio.to_thread(_download_chunk_closes, chunk_symbols)
                if not chunk_df.empty:
                    all_closes.append(chunk_df)
            except Exception as e:
                logger.warning(
                    "Breadth: chunk download failed for %d symbols (%s)",
                    len(chunk_symbols),
                    e,
                )

        if not all_closes:
            logger.warning("Breadth: no price data downloaded, skipping computation")
            return

        closes = pd.concat(all_closes, axis=1).sort_index()
        results = compute_breadth_from_closes(closes)

        computed_at = datetime.now(timezone.utc).isoformat()
        for key, values in results.items():
            await cache.set(CACHE_KEYS[key], {**values, "computed_at": computed_at}, ttl=None)

        logger.info(
            "Breadth: computed and cached s5fi=%s s5th=%s",
            results["s5fi"]["value"],
            results["s5th"]["value"],
        )
    except Exception as e:
        logger.error("Breadth: computation job failed: %s", e)


async def get_cached_breadth(key: str) -> Optional[Dict]:
    """Read a cached breadth indicator (s5fi | s5th) written by the background job."""
    cache_key = CACHE_KEYS.get(key)
    if not cache_key:
        return None
    return await cache.get(cache_key)


async def ensure_breadth_warm() -> None:
    """Run the breadth computation once if the cache is cold (startup hook)."""
    cached = await get_cached_breadth("s5fi")
    if cached:
        logger.info("Breadth: cache already warm, skipping startup computation")
        return
    logger.info("Breadth: cache cold, running startup computation")
    await compute_and_store_breadth()
