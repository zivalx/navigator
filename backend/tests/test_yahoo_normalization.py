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


@pytest.mark.xfail(strict=True, reason="needs Currency.ILS from Task 2")
@pytest.mark.asyncio
async def test_backfill_closes_normalized(db_session, monkeypatch):
    from app.tasks import scheduler as scheduler_module

    asset = Asset(
        id=str(uuid.uuid4()), symbol="TEVA.TA", name="Teva",
        exchange="TLV", currency=Currency.ILS,
        asset_type=AssetType.STOCK, market_region=MarketRegion.US,
    )
    db_session.add(asset)
    db_session.add(HoldingLot(
        id=str(uuid.uuid4()), asset_id=asset.id, quantity=1, avg_cost=60,
        cost_currency=Currency.ILS, account_name="a",
        purchase_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
    ))
    db_session.commit()
    asset_id = asset.id

    body = _chart_body({"currency": "ILA"}, [1_700_000_000, 1_700_086_400], [6400, 6512])
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        scheduler_module.httpx, "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))),
    )
    monkeypatch.setattr(scheduler_module, "SessionLocal", lambda: db_session)

    await scheduler_module.backfill_historical_prices()

    rows = (
        db_session.query(PriceSnapshot)
        .filter(PriceSnapshot.asset_id == asset_id)
        .order_by(PriceSnapshot.timestamp)
        .all()
    )
    assert [r.price for r in rows] == pytest.approx([64.0, 65.12])
    assert all(r.currency == Currency.ILS for r in rows)
