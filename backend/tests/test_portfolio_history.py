"""Tests for PortfolioService.get_nav_history() (GET /api/portfolio/history).

Covers the contract in docs/superpowers/specs/2026-07-23-portfolio-history-chart-design.md:
- NAV-at-date reuses the shared at-date valuation helper and respects each
  lot's purchase_date (a lot only contributes from its purchase date onward).
- pnl / pnl_pct are relative to the first point of the *requested period*.
- Dates with no snapshot are simply absent (no forward-fill).
- An empty portfolio returns an empty points list.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.asset import Asset, AssetType, Currency, MarketRegion
from app.models.holding import HoldingLot
from app.models.price import PriceSnapshot
from app.services.portfolio import PortfolioService

NOW = datetime.now(timezone.utc)


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


def make_lot(db, asset, quantity, purchase_date, avg_cost=1.0, account="main"):
    lot = HoldingLot(
        id=str(uuid.uuid4()),
        asset_id=asset.id,
        quantity=quantity,
        avg_cost=avg_cost,
        cost_currency=Currency.USD,
        account_name=account,
        purchase_date=purchase_date,
    )
    db.add(lot)
    db.commit()
    return lot


def make_snapshot(db, asset, price, days_ago, source="eod_snapshot"):
    snap = PriceSnapshot(
        id=str(uuid.uuid4()),
        asset_id=asset.id,
        price=price,
        currency=Currency.USD,
        timestamp=NOW - timedelta(days=days_ago),
        source=source,
    )
    db.add(snap)
    db.commit()
    return snap


@pytest.mark.asyncio
async def test_nav_series_respects_purchase_dates(db_session):
    """Asset B's contribution should only appear from its purchase date onward."""
    asset_a = make_asset(db_session, "AAA")
    asset_b = make_asset(db_session, "BBB")

    # Lot A purchased 10 days ago, present for the whole window.
    make_lot(db_session, asset_a, quantity=10, purchase_date=NOW - timedelta(days=10))
    # Lot B purchased 4 days ago - should NOT contribute before that.
    make_lot(db_session, asset_b, quantity=5, purchase_date=NOW - timedelta(days=4))

    snapshot_days_ago = [10, 8, 6, 4, 2, 0]
    for days_ago in snapshot_days_ago:
        make_snapshot(db_session, asset_a, price=10.0, days_ago=days_ago)
        make_snapshot(db_session, asset_b, price=20.0, days_ago=days_ago)

    service = PortfolioService(db_session)
    result = await service.get_nav_history(period="1m")

    assert result["base_currency"] == "USD"
    assert result["period"] == "1m"
    points = result["points"]

    # One point per distinct snapshot date, oldest first.
    assert [p["date"] for p in points] == sorted(p["date"] for p in points)
    assert len(points) == len(snapshot_days_ago)

    navs_by_days_ago = {
        days_ago: points[i]["nav"] for i, days_ago in enumerate(sorted(snapshot_days_ago, reverse=True))
    }

    # Before B's purchase date: only A's 10 * $10 = $100.
    assert navs_by_days_ago[10] == pytest.approx(100.0)
    assert navs_by_days_ago[8] == pytest.approx(100.0)
    assert navs_by_days_ago[6] == pytest.approx(100.0)
    # From B's purchase date onward: A's $100 + B's 5 * $20 = $100 -> $200.
    assert navs_by_days_ago[4] == pytest.approx(200.0)
    assert navs_by_days_ago[2] == pytest.approx(200.0)
    assert navs_by_days_ago[0] == pytest.approx(200.0)


@pytest.mark.asyncio
async def test_pnl_relative_to_first_point_of_period(db_session):
    asset = make_asset(db_session, "AAA")
    make_lot(db_session, asset, quantity=10, purchase_date=NOW - timedelta(days=30))

    prices_by_days_ago = {20: 10.0, 15: 11.0, 10: 12.0, 5: 15.0, 0: 20.0}
    for days_ago, price in prices_by_days_ago.items():
        make_snapshot(db_session, asset, price=price, days_ago=days_ago)

    service = PortfolioService(db_session)
    result = await service.get_nav_history(period="1m")
    points = result["points"]

    assert len(points) == 5
    first = points[0]
    assert first["pnl"] == pytest.approx(0.0)
    assert first["pnl_pct"] == pytest.approx(0.0)
    assert first["nav"] == pytest.approx(100.0)  # 10 shares * $10

    last = points[-1]
    assert last["nav"] == pytest.approx(200.0)  # 10 shares * $20
    assert last["pnl"] == pytest.approx(100.0)
    assert last["pnl_pct"] == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_period_window_filters_out_older_points(db_session):
    """pnl/pnl_pct must be relative to the first point *within the requested
    period*, not the earliest point ever recorded."""
    asset = make_asset(db_session, "AAA")
    make_lot(db_session, asset, quantity=1, purchase_date=NOW - timedelta(days=200))

    # A far-away snapshot outside the "1w" window, plus a few within it.
    make_snapshot(db_session, asset, price=1000.0, days_ago=60)
    make_snapshot(db_session, asset, price=50.0, days_ago=6)
    make_snapshot(db_session, asset, price=55.0, days_ago=3)
    make_snapshot(db_session, asset, price=60.0, days_ago=0)

    service = PortfolioService(db_session)
    result = await service.get_nav_history(period="1w")
    points = result["points"]

    assert len(points) == 3
    assert points[0]["nav"] == pytest.approx(50.0)
    assert points[0]["pnl"] == pytest.approx(0.0)
    assert points[-1]["nav"] == pytest.approx(60.0)
    assert points[-1]["pnl"] == pytest.approx(10.0)
    assert points[-1]["pnl_pct"] == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_no_forward_fill_for_missing_dates(db_session):
    """Dates with no snapshot are simply absent, not synthesized."""
    asset = make_asset(db_session, "AAA")
    make_lot(db_session, asset, quantity=1, purchase_date=NOW - timedelta(days=30))

    # Deliberately sparse/irregular snapshot dates (simulating weekends/holidays).
    make_snapshot(db_session, asset, price=10.0, days_ago=9)
    make_snapshot(db_session, asset, price=11.0, days_ago=5)
    make_snapshot(db_session, asset, price=12.0, days_ago=1)

    service = PortfolioService(db_session)
    result = await service.get_nav_history(period="1m")
    points = result["points"]

    assert len(points) == 3
    navs = [p["nav"] for p in points]
    assert navs == pytest.approx([10.0, 11.0, 12.0])


@pytest.mark.asyncio
async def test_empty_portfolio_returns_empty_points(db_session):
    service = PortfolioService(db_session)
    result = await service.get_nav_history(period="3m")

    assert result["points"] == []
    assert result["base_currency"] == "USD"
    assert result["period"] == "3m"


@pytest.mark.asyncio
async def test_default_period_is_3m(db_session):
    service = PortfolioService(db_session)
    result = await service.get_nav_history()
    assert result["period"] == "3m"
