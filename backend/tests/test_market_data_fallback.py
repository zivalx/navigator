"""Regression tests for MarketDataService.get_quote's last-resort fallback.

When every live provider fails, the service returns the last DB price
snapshot. That snapshot must be flagged `stale` (with its age) and must NOT
fabricate a 0.00 daily change, so a weeks-old close is never presented as a
live, flat-day quote.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.asset import Asset, AssetType, Currency, MarketRegion
from app.models.price import PriceSnapshot
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
