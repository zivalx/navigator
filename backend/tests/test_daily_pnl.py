"""Tests for the persisted daily P&L + EOD NAV series.

Covers docs/superpowers/specs/2026-08-20-daily-pnl-series-design.md:
- portfolio_snapshots model roundtrip
- compute_daily_snapshot: flow-adjusted daily P&L (a buy is not a gain),
  first-day baseline, upsert idempotency
- backfill_daily_snapshots: ordered series from price history, idempotent
- get_daily_pnl_series: window + display currency
- GET /api/portfolio/daily-pnl: period validation + cached series

Service tests use base_currency=USD so FX conversion short-circuits and the
suite stays offline.
"""
import datetime as dt
import uuid

import pytest

from app.models.asset import Asset, AssetType, Currency, MarketRegion
from app.models.holding import HoldingLot
from app.models.price import PriceSnapshot
from app.models.portfolio_snapshot import PortfolioSnapshot
from app.routers.portfolio import get_portfolio_daily_pnl
from app.services.portfolio import PortfolioService


def _asset(db, symbol="AAA"):
    a = Asset(
        id=str(uuid.uuid4()), symbol=symbol, name=f"{symbol} Inc.",
        exchange="X", currency=Currency.USD, asset_type=AssetType.STOCK,
        market_region=MarketRegion.US,
    )
    db.add(a); db.commit(); return a


def _lot(db, asset, qty, purchase_date, avg_cost=50.0):
    lot = HoldingLot(
        id=str(uuid.uuid4()), asset_id=asset.id, quantity=qty,
        avg_cost=avg_cost, cost_currency=Currency.USD,
        account_name="m", purchase_date=purchase_date,
    )
    db.add(lot); db.commit(); return lot


def _snap(db, asset, price, day):
    db.add(PriceSnapshot(
        id=str(uuid.uuid4()), asset_id=asset.id, price=price,
        currency=Currency.USD,
        timestamp=dt.datetime.combine(day, dt.time(23, 0), tzinfo=dt.timezone.utc),
        source="eod_snapshot",
    ))
    db.commit()


@pytest.fixture()
def stub_cache(monkeypatch):
    store = {}

    async def _get(k):
        return store.get(k)

    async def _set(k, v, ttl=60):
        store[k] = v

    monkeypatch.setattr("app.routers.portfolio.cache.get", _get)
    monkeypatch.setattr("app.routers.portfolio.cache.set", _set)
    return store


# ---------------------------------------------------------------------------
# Task 1 — model
# ---------------------------------------------------------------------------

def test_portfolio_snapshot_row_roundtrips(db_session):
    row = PortfolioSnapshot(
        id=str(uuid.uuid4()),
        date=dt.date(2026, 8, 20),
        nav=1000.0,
        net_flow=0.0,
        daily_pnl=25.0,
        daily_pnl_pct=2.5,
        base_currency="USD",
    )
    db_session.add(row)
    db_session.commit()

    stored = db_session.query(PortfolioSnapshot).filter_by(date=dt.date(2026, 8, 20)).one()
    assert stored.nav == 1000.0
    assert stored.daily_pnl == 25.0
    assert stored.base_currency == "USD"


# ---------------------------------------------------------------------------
# Task 3 — compute_daily_snapshot
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_daily_snapshot_pure_market_move(db_session):
    a = _asset(db_session)
    _lot(db_session, a, qty=10, purchase_date=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc))
    _snap(db_session, a, price=100.0, day=dt.date(2026, 8, 18))
    _snap(db_session, a, price=110.0, day=dt.date(2026, 8, 19))
    svc = PortfolioService(db_session, base_currency=Currency.USD)

    day18 = await svc.compute_daily_snapshot(dt.date(2026, 8, 18))
    day19 = await svc.compute_daily_snapshot(dt.date(2026, 8, 19))

    assert day18.nav == pytest.approx(1000.0)
    assert day18.daily_pnl == pytest.approx(0.0)   # first day: no baseline
    assert day19.nav == pytest.approx(1100.0)
    assert day19.net_flow == pytest.approx(0.0)
    assert day19.daily_pnl == pytest.approx(100.0)
    assert day19.daily_pnl_pct == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_daily_snapshot_buy_is_not_a_gain(db_session):
    a = _asset(db_session)
    _lot(db_session, a, qty=10, purchase_date=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc))
    _snap(db_session, a, price=100.0, day=dt.date(2026, 8, 18))
    await PortfolioService(db_session, base_currency=Currency.USD).compute_daily_snapshot(dt.date(2026, 8, 18))
    # On the 19th, buy 5 more shares (a flow) at an unchanged price.
    _lot(db_session, a, qty=5, purchase_date=dt.datetime(2026, 8, 19, tzinfo=dt.timezone.utc), avg_cost=100.0)
    _snap(db_session, a, price=100.0, day=dt.date(2026, 8, 19))

    day19 = await PortfolioService(db_session, base_currency=Currency.USD).compute_daily_snapshot(dt.date(2026, 8, 19))

    assert day19.nav == pytest.approx(1500.0)      # 15 shares * 100
    assert day19.net_flow == pytest.approx(500.0)  # bought 5 * 100
    assert day19.daily_pnl == pytest.approx(0.0)   # 1500 - 1000 - 500
    assert day19.daily_pnl_pct == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_daily_snapshot_ignores_uneven_snapshot_coverage(db_session):
    """A second asset whose price history only begins on day 2 must NOT show
    as a huge gain that day (the coverage-artifact bug). Only assets with a
    price on BOTH days contribute to daily P&L."""
    a = _asset(db_session, symbol="AAA")
    b = _asset(db_session, symbol="BBB")
    _lot(db_session, a, qty=10, purchase_date=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc))
    _lot(db_session, b, qty=100, purchase_date=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc))
    # Day 1: only AAA has a snapshot.
    _snap(db_session, a, price=100.0, day=dt.date(2026, 8, 18))
    # Day 2: AAA flat, BBB's price history appears for the first time at $50.
    _snap(db_session, a, price=100.0, day=dt.date(2026, 8, 19))
    _snap(db_session, b, price=50.0, day=dt.date(2026, 8, 19))
    svc = PortfolioService(db_session, base_currency=Currency.USD)

    await svc.compute_daily_snapshot(dt.date(2026, 8, 18))
    day19 = await svc.compute_daily_snapshot(dt.date(2026, 8, 19))

    # NAV jumps (BBB now valued) but daily P&L is ~0: AAA flat, BBB had no
    # prior price so it isn't counted as a gain.
    assert day19.nav == pytest.approx(100 * 10 + 50 * 100)  # 6000
    assert day19.daily_pnl == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_daily_snapshot_upsert_is_idempotent(db_session):
    a = _asset(db_session)
    _lot(db_session, a, qty=10, purchase_date=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc))
    _snap(db_session, a, price=100.0, day=dt.date(2026, 8, 18))
    svc = PortfolioService(db_session, base_currency=Currency.USD)
    await svc.compute_daily_snapshot(dt.date(2026, 8, 18))
    await svc.compute_daily_snapshot(dt.date(2026, 8, 18))
    assert db_session.query(PortfolioSnapshot).filter_by(date=dt.date(2026, 8, 18)).count() == 1


# ---------------------------------------------------------------------------
# Task 4 — backfill
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backfill_builds_ordered_series_and_is_idempotent(db_session):
    a = _asset(db_session)
    _lot(db_session, a, qty=10, purchase_date=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc))
    _snap(db_session, a, price=100.0, day=dt.date(2026, 8, 17))
    _snap(db_session, a, price=105.0, day=dt.date(2026, 8, 18))
    _snap(db_session, a, price=105.0, day=dt.date(2026, 8, 19))
    svc = PortfolioService(db_session, base_currency=Currency.USD)

    written = await svc.backfill_daily_snapshots()
    rows = db_session.query(PortfolioSnapshot).order_by(PortfolioSnapshot.date).all()

    assert written == 3
    assert [r.date for r in rows] == [dt.date(2026, 8, 17), dt.date(2026, 8, 18), dt.date(2026, 8, 19)]
    assert rows[0].daily_pnl == pytest.approx(0.0)   # first day
    assert rows[1].daily_pnl == pytest.approx(50.0)  # +5 * 10 shares
    assert rows[2].daily_pnl == pytest.approx(0.0)   # flat

    await svc.backfill_daily_snapshots()
    assert db_session.query(PortfolioSnapshot).count() == 3


# ---------------------------------------------------------------------------
# Task 5 — series read
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_daily_pnl_series_returns_window_in_usd(db_session):
    a = _asset(db_session)
    _lot(db_session, a, qty=10, purchase_date=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc))
    for day, price in [(dt.date(2026, 8, 18), 100.0), (dt.date(2026, 8, 19), 110.0)]:
        _snap(db_session, a, price=price, day=day)
    svc = PortfolioService(db_session, base_currency=Currency.USD)
    await svc.backfill_daily_snapshots()

    result = await svc.get_daily_pnl_series("1y")

    assert result["base_currency"] == "USD"
    assert result["period"] == "1y"
    assert len(result["points"]) == 2
    last = result["points"][-1]
    assert last["nav"] == pytest.approx(1100.0)
    assert last["dailyPnL"] == pytest.approx(100.0)
    assert last["dailyPnLPercent"] == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Task 6 — endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_daily_pnl_endpoint_invalid_period_400(db_session):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await get_portfolio_daily_pnl(period="bogus", base_currency=Currency.USD, db=db_session)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_daily_pnl_endpoint_returns_series(db_session, stub_cache):
    a = _asset(db_session)
    _lot(db_session, a, qty=10, purchase_date=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc))
    for day, price in [(dt.date(2026, 8, 18), 100.0), (dt.date(2026, 8, 19), 110.0)]:
        _snap(db_session, a, price=price, day=day)
    await PortfolioService(db_session, base_currency=Currency.USD).backfill_daily_snapshots()

    result = await get_portfolio_daily_pnl(period="1y", base_currency=Currency.USD, db=db_session)
    assert len(result["points"]) == 2
    assert result["points"][-1]["dailyPnL"] == pytest.approx(100.0)
    assert set(stub_cache.keys()) == {"portfolio:daily-pnl:1y:USD"}
