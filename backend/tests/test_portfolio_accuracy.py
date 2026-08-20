"""Financial-accuracy regression tests for PortfolioService.

Guards three bugs found in the whole-codebase audit:
- B1: daily price change must be converted to the base currency (it was
  passed through in the asset's native currency while everything around it
  was base-converted).
- avgCostBase: a base-currency cost basis must be exposed per holding so the
  frontend can compute unrealized-% without mixing currencies.
- B2: a lot purchased *after* the comparison date must not be counted as a
  gain — both sides of a daily/weekly/monthly P&L delta must cover the same
  set of lots.

FX and market-data network calls are stubbed (same approach as
test_portfolio_base_currency.py): FxService falls back to a seeded FxRate
row, and MarketDataService.get_quote returns a fixed quote.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.asset import Asset, AssetType, Currency, MarketRegion
from app.models.holding import HoldingLot
from app.models.price import FxRate, PriceSnapshot
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
    db.add(FxRate(
        id=str(uuid.uuid4()),
        base_currency=base,
        quote_currency=quote,
        rate=rate,
        timestamp=NOW,
    ))
    db.commit()


def seed_snapshot(db, asset, price, days_ago, currency=Currency.USD):
    db.add(PriceSnapshot(
        id=str(uuid.uuid4()),
        asset_id=asset.id,
        price=price,
        currency=currency,
        timestamp=NOW - timedelta(days=days_ago),
        source="eod_snapshot",
    ))
    db.commit()


@pytest.fixture(autouse=True)
def no_live_fx(monkeypatch):
    async def _raise(self, from_currency, to_currency):
        raise RuntimeError("network disabled in tests")

    monkeypatch.setattr(FxService, "_fetch_rate", _raise)


def fixed_quote(monkeypatch, price=100.0, change=0.0, currency="USD"):
    async def _fake_get_quote(self, symbol, asset_type=None):
        return {
            "price": price,
            "change": change,
            "changePercent": (change / (price - change) * 100) if price != change else 0.0,
            "currency": currency,
        }

    monkeypatch.setattr(MarketDataService, "get_quote", _fake_get_quote)


# ---------------------------------------------------------------------------
# B1 — price change converted to base currency
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_price_change_is_converted_to_base_currency(db_session, monkeypatch):
    """A native-currency daily change must be returned in the base currency,
    so `quantity * priceChange` sums correctly against a base-currency NAV."""
    asset = make_asset(db_session, currency=Currency.EUR)
    make_lot(db_session, asset, quantity=10, avg_cost=90.0, currency=Currency.EUR)
    # Quote is in EUR: price 110, up €2 today.
    fixed_quote(monkeypatch, price=110.0, change=2.0, currency="EUR")
    # 1 EUR = 1.10 USD.
    seed_fx_rate(db_session, Currency.EUR, Currency.USD, 1.10)

    holdings = await PortfolioService(db_session, base_currency=Currency.USD).get_holdings_with_prices()

    # €2.00 change * 1.10 = $2.20 in base currency.
    assert holdings[0].price_change == pytest.approx(2.20)
    # percentage is currency-invariant.
    assert holdings[0].price_change_percent == pytest.approx(2.0 / 108.0 * 100)


@pytest.mark.asyncio
async def test_avg_cost_base_is_exposed_in_base_currency(db_session, monkeypatch):
    asset = make_asset(db_session, currency=Currency.EUR)
    make_lot(db_session, asset, quantity=10, avg_cost=100.0, currency=Currency.EUR)
    fixed_quote(monkeypatch, price=110.0, change=0.0, currency="EUR")
    seed_fx_rate(db_session, Currency.EUR, Currency.USD, 1.10)

    holdings = await PortfolioService(db_session, base_currency=Currency.USD).get_holdings_with_prices()

    # €100 cost * 1.10 = $110 base cost basis per share.
    assert holdings[0].avg_cost_base == pytest.approx(110.0)
    # native avgCost preserved for per-lot display.
    assert holdings[0].avg_cost == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# B2 — a newly-purchased lot must not read as a gain
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_new_lot_not_counted_as_daily_gain(db_session, monkeypatch):
    """Old lot rose 100→110 (a real +$100 on 10 shares). A second lot bought
    *today* must not add its full market value to the daily gain."""
    asset = make_asset(db_session)
    old_lot = make_lot(db_session, asset, quantity=10, purchase_date=NOW - timedelta(days=30))
    # Yesterday's snapshot for the old lot.
    seed_snapshot(db_session, asset, price=100.0, days_ago=1)
    # A new lot bought today.
    make_lot(db_session, asset, quantity=5, purchase_date=NOW)
    # Today's quote: 110.
    fixed_quote(monkeypatch, price=110.0, change=10.0)

    summary = await PortfolioService(db_session, base_currency=Currency.USD).get_summary()

    # total NAV counts both lots: 15 * 110 = 1650.
    assert summary.total_nav == pytest.approx(1650.0)
    # Daily P&L must reflect ONLY the old lot's move: 10*110 - 10*100 = 100,
    # NOT 1650 - 1000 = 650 (which would count the new lot as appreciation).
    assert summary.daily_pnl == pytest.approx(100.0)
    assert summary.daily_pnl_percent == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_daily_pnl_normal_case_unaffected(db_session, monkeypatch):
    """Sanity: with no new lots, daily P&L is the straightforward NAV diff."""
    asset = make_asset(db_session)
    make_lot(db_session, asset, quantity=10, purchase_date=NOW - timedelta(days=30))
    seed_snapshot(db_session, asset, price=100.0, days_ago=1)
    fixed_quote(monkeypatch, price=110.0, change=10.0)

    summary = await PortfolioService(db_session, base_currency=Currency.USD).get_summary()

    assert summary.total_nav == pytest.approx(1100.0)
    assert summary.daily_pnl == pytest.approx(100.0)
    assert summary.daily_pnl_percent == pytest.approx(10.0)
