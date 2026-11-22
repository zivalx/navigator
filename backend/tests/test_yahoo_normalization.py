import uuid
from datetime import datetime, timezone

import httpx
import pytest

from app.models.asset import Asset, AssetType, Currency, MarketRegion
from app.models.holding import HoldingLot
from app.models.price import PriceSnapshot
from app.providers.yahoo import YahooFinanceProvider


def _chart_body(meta, timestamps=None, closes=None):
    result = {"meta": meta}
    if timestamps is not None:
        result["timestamp"] = timestamps
        result["indicators"] = {"quote": [{"close": closes}]}
    return {"chart": {"result": [result]}}


def _provider(handler):
    provider = YahooFinanceProvider()
    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return provider


TLV_META = {
    "currency": "ILA",
    "exchangeName": "TLV",
    "regularMarketPrice": 6512.0,
    "chartPreviousClose": 6400.0,
}


@pytest.mark.asyncio
async def test_get_quote_normalizes_agorot():
    provider = _provider(lambda req: httpx.Response(200, json=_chart_body(TLV_META)))
    quote = await provider.get_quote("TEVA.TA")
    assert quote["price"] == 65.12
    assert quote["change"] == pytest.approx(1.12)
    assert quote["currency"] == "ILS"
    assert quote["unitFactor"] == 0.01
    assert quote["changePercent"] == 1.75


@pytest.mark.asyncio
async def test_get_quote_usd_unchanged():
    meta = {"currency": "USD", "regularMarketPrice": 150.0, "chartPreviousClose": 148.0}
    provider = _provider(lambda req: httpx.Response(200, json=_chart_body(meta)))
    quote = await provider.get_quote("AAPL")
    assert quote["unitFactor"] == 1.0
    assert quote["price"] == 150.0
    assert quote["change"] == 2.0
    assert quote["currency"] == "USD"


@pytest.mark.asyncio
async def test_get_chart_meta_tlv():
    seen = {}

    def handler(req):
        seen["params"] = dict(req.url.params)
        return httpx.Response(200, json=_chart_body(TLV_META))

    meta = await _provider(handler).get_chart_meta("TEVA.TA")
    assert meta["currency"] == "ILS"
    assert meta["unitFactor"] == 0.01
    assert meta["exchangeName"] == "TLV"
    assert meta["fullExchangeName"] is None
    assert meta["instrumentType"] is None
    assert seen["params"]["range"] == "1d"


@pytest.mark.asyncio
async def test_get_chart_meta_none_on_404_and_empty():
    p404 = _provider(lambda req: httpx.Response(404, json={}))
    assert await p404.get_chart_meta("NOPE") is None
    pempty = _provider(lambda req: httpx.Response(200, json={"chart": {"result": None}}))
    assert await pempty.get_chart_meta("NOPE") is None


@pytest.mark.asyncio
async def test_get_chart_meta_raises_on_network_failure():
    def handler(req):
        raise httpx.ConnectError("boom")

    with pytest.raises(httpx.HTTPError):
        await _provider(handler).get_chart_meta("AAPL")


@pytest.mark.asyncio
async def test_quote_cache_key_is_v2(db_session, monkeypatch):
    from app.cache import cache
    from app.services.market_data import MarketDataService

    keys = []

    async def fake_get(key):
        keys.append(key)
        return {"symbol": "X", "price": 1}

    monkeypatch.setattr(cache, "get", fake_get)
    await MarketDataService(db_session).get_quote("teva.ta")
    assert keys == ["quote:v2:TEVA.TA"]


@pytest.mark.asyncio
async def test_search_does_not_invent_usd_currency():
    """Yahoo search results carry no currency; a TASE hit must not come
    back labelled USD (callers take the currency from the quote)."""
    body = {"quotes": [
        {"symbol": "TEVA.TA", "shortname": "TEVA", "exchange": "TLV", "quoteType": "EQUITY"},
    ]}
    provider = _provider(lambda req: httpx.Response(200, json=body))
    [hit] = await provider.search("teva")
    assert hit["symbol"] == "TEVA.TA"
    assert hit["currency"] is None


@pytest.mark.asyncio
async def test_yahoo_quote_carries_market_time():
    meta = {
        "currency": "USD",
        "regularMarketPrice": 150.0,
        "chartPreviousClose": 148.0,
        "regularMarketTime": 1763740800,
        "exchangeTimezoneName": "America/New_York",
    }
    provider = _provider(lambda req: httpx.Response(200, json=_chart_body(meta)))
    quote = await provider.get_quote("AAPL")
    assert quote["marketTime"] == "2025-11-21T16:00:00+00:00"
    assert quote["exchangeTimezone"] == "America/New_York"


@pytest.mark.asyncio
async def test_provider_timestamps_are_aware_utc():
    from app.providers.coingecko import CoinGeckoProvider
    from app.providers.finnhub import FinnhubProvider
    from app.providers.polygon import PolygonProvider

    def parse(v):
        return v if isinstance(v, datetime) else datetime.fromisoformat(v)

    def client(body):
        return httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body))
        )

    y = _provider(lambda req: httpx.Response(200, json=_chart_body(
        {"currency": "USD", "regularMarketPrice": 1.0, "chartPreviousClose": 1.0})))
    cg = CoinGeckoProvider()
    cg.client = client({"bitcoin": {"usd": 100.0, "usd_24h_change": 1.0, "usd_24h_vol": 5}})
    pg = PolygonProvider(api_key="k")
    pg.client = client({"status": "OK", "results": [{"c": 2.0, "o": 1.0, "t": 1763740800000}]})
    fh = FinnhubProvider(api_key="k")
    fh.client = client({"c": 2.0, "d": 1.0, "dp": 1.0, "t": 1763740800})

    for q in (await y.get_quote("AAPL"), await cg.get_quote("BTC"),
              await pg.get_quote("AAPL"), await fh.get_quote("AAPL")):
        assert parse(q["timestamp"]).utcoffset().total_seconds() == 0
