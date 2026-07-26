"""Tests for wiring the portfolio base currency end-to-end.

Covers:
- PortfolioService.get_summary() actually converts NAV using the requested
  base_currency (not just labeling the response with it).
- The GET /portfolio/history router caches per base_currency - two calls
  with different currencies must not collide on the same cache entry.

FX and market-data network calls are stubbed out so the tests are
deterministic and offline: FxService is forced to fall back to a
DB-seeded FxRate row (its normal fallback path when the live API call
fails), and MarketDataService.get_quote is replaced with a fixed quote.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.asset import Asset, AssetType, Currency, MarketRegion
from app.models.holding import HoldingLot
from app.models.price import FxRate
from app.routers.portfolio import get_portfolio_history
from app.services.fx import FxService
from app.services.market_data import MarketDataService
from app.services.portfolio import PortfolioService

NOW = datetime.now(timezone.utc)


def make_asset(db, symbol="AAA", currency=Currency.USD):
    asset = Asset(
        id=str(uuid.uuid4()),
        symbol=symbol,
        name=f"{symbol} Inc.",
        exchange="NASDAQ",
        currency=currency,
        asset_type=AssetType.STOCK,
        market_region=MarketRegion.US,
    )
    db.add(asset)
    db.commit()
    return asset


def make_lot(db, asset, quantity, avg_cost=50.0, purchase_date=None, currency=Currency.USD):
    lot = HoldingLot(
        id=str(uuid.uuid4()),
        asset_id=asset.id,
        quantity=quantity,
        avg_cost=avg_cost,
        cost_currency=currency,
        account_name="main",
        purchase_date=purchase_date or (NOW - timedelta(days=30)),
    )
    db.add(lot)
    db.commit()
    return lot


def seed_fx_rate(db, base, quote, rate):
    fx = FxRate(
        id=str(uuid.uuid4()),
        base_currency=base,
        quote_currency=quote,
        rate=rate,
        timestamp=NOW,
    )
    db.add(fx)
    db.commit()
    return fx


@pytest.fixture(autouse=True)
def no_live_fx(monkeypatch):
    """Force FxService to skip the live rate API and use its normal DB
    fallback path instead, so tests don't depend on network access."""

    async def _raise(self, from_currency, to_currency):
        raise RuntimeError("network disabled in tests")

    monkeypatch.setattr(FxService, "_fetch_rate", _raise)


@pytest.fixture(autouse=True)
def fixed_quote(monkeypatch):
    """Deterministic $100/share quote with no daily change, so any NAV
    difference across base_currency comes purely from FX conversion."""

    async def _fake_get_quote(self, symbol, asset_type=None):
        return {"price": 100.0, "change": 0.0, "changePercent": 0.0, "currency": "USD"}

    monkeypatch.setattr(MarketDataService, "get_quote", _fake_get_quote)


@pytest.fixture()
def fake_history_cache(monkeypatch):
    """An in-memory stand-in for the Redis cache used by the /history
    route, so we can actually observe collisions/non-collisions between
    cache keys without a real Redis instance."""
    store: dict = {}

    async def _get(key):
        return store.get(key)

    async def _set(key, value, ttl=60):
        store[key] = value

    monkeypatch.setattr("app.routers.portfolio.cache.get", _get)
    monkeypatch.setattr("app.routers.portfolio.cache.set", _set)
    return store


@pytest.mark.asyncio
async def test_summary_nav_converts_with_base_currency(db_session):
    """Same USD holdings should produce different NAVs depending on the
    requested base_currency, using the seeded USD->EUR FX rate."""
    asset = make_asset(db_session)
    make_lot(db_session, asset, quantity=10, avg_cost=50.0)
    seed_fx_rate(db_session, Currency.USD, Currency.EUR, 0.5)

    usd_summary = await PortfolioService(db_session, base_currency=Currency.USD).get_summary()
    eur_summary = await PortfolioService(db_session, base_currency=Currency.EUR).get_summary()

    assert usd_summary.base_currency == "USD"
    assert eur_summary.base_currency == "EUR"

    # 10 shares * $100 quote = $1000 in USD terms.
    assert usd_summary.total_nav == pytest.approx(1000.0)
    # Converted at the seeded USD->EUR rate of 0.5.
    assert eur_summary.total_nav == pytest.approx(500.0)
    assert eur_summary.total_nav != usd_summary.total_nav


@pytest.mark.asyncio
async def test_holdings_and_grouped_holdings_convert_with_base_currency(db_session):
    asset = make_asset(db_session)
    make_lot(db_session, asset, quantity=4, avg_cost=25.0)
    seed_fx_rate(db_session, Currency.USD, Currency.EUR, 0.5)

    usd_service = PortfolioService(db_session, base_currency=Currency.USD)
    eur_service = PortfolioService(db_session, base_currency=Currency.EUR)

    usd_holdings = await usd_service.get_holdings_with_prices()
    eur_holdings = await eur_service.get_holdings_with_prices()

    assert usd_holdings[0].current_price == pytest.approx(100.0)
    assert eur_holdings[0].current_price == pytest.approx(50.0)
    assert usd_holdings[0].market_value == pytest.approx(400.0)
    assert eur_holdings[0].market_value == pytest.approx(200.0)

    usd_grouped = await usd_service.get_grouped_holdings()
    eur_grouped = await eur_service.get_grouped_holdings()

    assert usd_grouped[0].market_value == pytest.approx(400.0)
    assert eur_grouped[0].market_value == pytest.approx(200.0)


@pytest.mark.asyncio
async def test_history_cache_keys_do_not_collide_across_currencies(db_session, fake_history_cache):
    """Regression guard: the /history route must key its cache on
    base_currency. If it didn't, the second call below would incorrectly
    be served the first call's cached (wrong-currency) values."""
    asset = make_asset(db_session)
    make_lot(db_session, asset, quantity=10, purchase_date=NOW - timedelta(days=10))
    seed_fx_rate(db_session, Currency.USD, Currency.EUR, 0.5)

    # Give the asset a couple of price snapshots so get_nav_history has
    # points to return.
    from app.models.price import PriceSnapshot

    for days_ago, price in [(5, 20.0), (0, 30.0)]:
        snap = PriceSnapshot(
            id=str(uuid.uuid4()),
            asset_id=asset.id,
            price=price,
            currency=Currency.USD,
            timestamp=NOW - timedelta(days=days_ago),
            source="eod_snapshot",
        )
        db_session.add(snap)
    db_session.commit()

    usd_result = await get_portfolio_history(
        period="1m", base_currency=Currency.USD, db=db_session
    )
    eur_result = await get_portfolio_history(
        period="1m", base_currency=Currency.EUR, db=db_session
    )

    assert usd_result["base_currency"] == "USD"
    assert eur_result["base_currency"] == "EUR"
    assert len(usd_result["points"]) == 2
    assert len(eur_result["points"]) == 2

    # Same underlying data, different currency -> different NAV values.
    for usd_point, eur_point in zip(usd_result["points"], eur_result["points"]):
        assert eur_point["nav"] == pytest.approx(usd_point["nav"] * 0.5)
        assert eur_point["nav"] != usd_point["nav"]

    # Two distinct cache entries were written - one per currency.
    assert len(fake_history_cache) == 2
    assert set(fake_history_cache.keys()) == {
        "portfolio:history:1m:USD",
        "portfolio:history:1m:EUR",
    }

    # Calling USD again should now be served from that currency's own
    # cache entry (and still be correct) rather than the EUR one.
    usd_result_again = await get_portfolio_history(
        period="1m", base_currency=Currency.USD, db=db_session
    )
    assert usd_result_again["base_currency"] == "USD"
    assert usd_result_again["points"][-1]["nav"] == pytest.approx(usd_result["points"][-1]["nav"])
