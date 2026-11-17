"""Regression tests for MarketDataService.get_quote's last-resort fallback.

When every live provider fails, the service returns the last DB price
snapshot. That snapshot must be flagged `stale` (with its age) and must NOT
fabricate a 0.00 daily change, so a weeks-old close is never presented as a
live, flat-day quote.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.asset import Asset, AssetType, Currency, MarketRegion
from app.models.price import PriceSnapshot
from app.providers import FinnhubProvider, PolygonProvider
from app.services.market_data import MarketDataService

NOW = datetime.now(timezone.utc)


def _all_providers_down(db, monkeypatch):
    """Build a MarketDataService with every (configured) provider forced to
    fail and the Redis cache neutralized, so get_quote is driven to its DB
    fallback deterministically regardless of which API keys are in .env."""
    async def _fail(*args, **kwargs):
        raise RuntimeError("provider down")

    async def _cache_miss(*args, **kwargs):
        return None

    monkeypatch.setattr("app.services.market_data.cache.get", _cache_miss)
    monkeypatch.setattr("app.services.market_data.cache.set", _cache_miss)

    service = MarketDataService(db)
    for name in ("yahoo", "coingecko", "polygon", "finnhub", "alphavantage"):
        provider = getattr(service, name, None)
        if provider is not None:
            monkeypatch.setattr(provider, "get_quote", _fail, raising=False)
    return service


def make_asset(db, symbol="AAA"):
    asset = Asset(
        id=str(uuid.uuid4()),
        symbol=symbol,
        name=f"{symbol} Inc.",
        exchange="NASDAQ",
        currency=Currency.USD,
        asset_type=AssetType.STOCK,
        market_region=MarketRegion.US,
    )
    db.add(asset)
    db.commit()
    return asset


@pytest.mark.asyncio
async def test_stale_db_fallback_is_flagged_not_a_flat_quote(db_session, monkeypatch):
    asset = make_asset(db_session)
    db_session.add(PriceSnapshot(
        id=str(uuid.uuid4()),
        asset_id=asset.id,
        price=123.45,
        currency=Currency.USD,
        timestamp=NOW - timedelta(days=9),
        source="eod_snapshot",
    ))
    db_session.commit()

    service = _all_providers_down(db_session, monkeypatch)

    quote = await service.get_quote("AAA", "stock")

    assert quote["source"] == "db_cache"
    assert quote["price"] == pytest.approx(123.45)
    assert quote["stale"] is True
    # Unknown change, NOT a fabricated flat 0.00.
    assert quote["change"] is None
    assert quote["changePercent"] is None
    assert quote["ageSeconds"] > 8 * 86400


@pytest.mark.asyncio
async def test_no_snapshot_still_raises(db_session, monkeypatch):
    make_asset(db_session, symbol="BBB")
    service = _all_providers_down(db_session, monkeypatch)

    with pytest.raises(ValueError):
        await service.get_quote("BBB", "stock")


def _make_ils_asset(db, symbol="TEVA.TA"):
    asset = Asset(
        id=str(uuid.uuid4()),
        symbol=symbol,
        name=f"{symbol} Ltd.",
        exchange="TLV",
        currency=Currency.ILS,
        asset_type=AssetType.STOCK,
        market_region=MarketRegion.IL,
    )
    db.add(asset)
    db.commit()
    return asset


def _with_usd_fallbacks(db, monkeypatch):
    """Service whose Yahoo fails while Polygon and Finnhub are configured and
    would return USD prices. Returns (service, calls) where calls records which
    fallback providers were invoked."""
    async def _fail(*args, **kwargs):
        raise RuntimeError("provider down")

    async def _cache_miss(*args, **kwargs):
        return None

    monkeypatch.setattr("app.services.market_data.cache.get", _cache_miss)
    monkeypatch.setattr("app.services.market_data.cache.set", _cache_miss)

    service = MarketDataService(db)
    service.polygon = PolygonProvider(api_key="test-key")
    service.finnhub = FinnhubProvider(api_key="test-key")
    calls = []

    def _usd_quote(name):
        async def _get_quote(symbol, *args, **kwargs):
            calls.append(name)
            return {"symbol": symbol, "price": 10.0, "change": 0.1,
                    "changePercent": 1.0, "currency": "USD",
                    "timestamp": NOW}
        return _get_quote

    monkeypatch.setattr(service.yahoo, "get_quote", _fail)
    monkeypatch.setattr(service.polygon, "get_quote", _usd_quote("polygon"))
    monkeypatch.setattr(service.finnhub, "get_quote", _usd_quote("finnhub"))
    return service, calls


@pytest.mark.asyncio
async def test_non_usd_asset_skips_polygon_and_finnhub(db_session, monkeypatch):
    asset = _make_ils_asset(db_session)
    db_session.add(PriceSnapshot(
        id=str(uuid.uuid4()),
        asset_id=asset.id,
        price=55.5,
        currency=Currency.ILS,
        timestamp=NOW - timedelta(hours=2),
        source="eod_snapshot",
    ))
    db_session.commit()
    service, calls = _with_usd_fallbacks(db_session, monkeypatch)

    quote = await service.get_quote("TEVA.TA", "stock")

    assert calls == []
    assert quote["source"] == "db_cache"
    assert quote["currency"] == "ILS"
    assert quote["price"] == pytest.approx(55.5)


@pytest.mark.asyncio
async def test_usd_asset_still_uses_polygon(db_session, monkeypatch):
    make_asset(db_session, symbol="CCC")
    service, calls = _with_usd_fallbacks(db_session, monkeypatch)

    quote = await service.get_quote("CCC", "stock")

    assert calls == ["polygon"]
    assert quote["source"] == "polygon"
    assert quote["currency"] == "USD"


@pytest.mark.asyncio
async def test_snapshot_store_failure_logs_warning(db_session, monkeypatch, caplog):
    make_asset(db_session, symbol="DDD")
    service = MarketDataService(db_session)

    def _boom(*args, **kwargs):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(db_session, "add", _boom)

    with caplog.at_level(logging.WARNING, logger="app.services.market_data"):
        await service._store_price_snapshot("DDD", {"price": 1.0, "currency": "USD"})

    assert any(
        "could not store price snapshot for DDD" in r.getMessage()
        and "db exploded" in r.getMessage()
        for r in caplog.records
    )
