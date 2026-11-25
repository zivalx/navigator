"""Portfolio valuation at the day's FX rate (stored fx_daily_rates)."""
import datetime as dt
import uuid

import pytest

from app.models import FxDailyRate
from app.models.asset import Currency
from app.models.portfolio_snapshot import PortfolioSnapshot
from app.services.portfolio import PortfolioService
from tests.test_portfolio_unavailable import (  # noqa: F401  (fixtures + helpers)
    NOW, ils_quote, make_ils_asset, make_lot, make_snapshot, quotes, rates,
)

UTC = dt.timezone.utc


def seed_ils(db, day, per_usd):
    db.add(FxDailyRate(
        id=str(uuid.uuid4()), currency=Currency.ILS, date=day, per_usd=per_usd,
        source="yahoo", fetched_at=NOW,
    ))
    db.commit()


def eod(day):
    return dt.datetime.combine(day, dt.time(23, 0), tzinfo=UTC)


@pytest.mark.asyncio
async def test_nav_at_date_uses_that_days_rate(db_session):
    d1 = (NOW - dt.timedelta(days=5)).date()
    d2 = d1 + dt.timedelta(days=1)
    seed_ils(db_session, d1, 4.0)
    seed_ils(db_session, d2, 3.2)
    asset = make_ils_asset(db_session)
    make_lot(db_session, asset, 10, 90.0, Currency.ILS,
             purchase_date=eod(d1) - dt.timedelta(days=10))
    make_snapshot(db_session, asset, 100.0, Currency.ILS, eod(d1))
    make_snapshot(db_session, asset, 100.0, Currency.ILS, eod(d2))

    service = PortfolioService(db_session, base_currency=Currency.USD, strict_fx=True)
    assert await service._get_nav_at_date(eod(d1)) == pytest.approx(10 * 25.0)
    assert await service._get_nav_at_date(eod(d2)) == pytest.approx(10 * 31.25)


@pytest.mark.asyncio
async def test_lot_cost_uses_purchase_date_rate(db_session, rates, quotes):
    buy = (NOW - dt.timedelta(days=20)).date()
    seed_ils(db_session, buy, 3.5)
    rates[("ILS", "USD")] = 1 / 3.2
    asset = make_ils_asset(db_session)
    lot = make_lot(db_session, asset, 10, 350.0, Currency.ILS,
                   purchase_date=dt.datetime.combine(buy, dt.time(10, 0), tzinfo=UTC))
    quotes["TEVA.TA"] = ils_quote(350.0)

    v = await PortfolioService(db_session, base_currency=Currency.USD)._value_lot(lot)

    assert v["avgCostBase"] == pytest.approx(100.0)
    assert v["currentPrice"] == pytest.approx(109.375)
    assert v["unrealizedPnL"] > 0


@pytest.mark.asyncio
async def test_lot_cost_naive_purchase_date(db_session, rates, quotes):
    buy = (NOW - dt.timedelta(days=20)).date()
    seed_ils(db_session, buy, 3.5)
    rates[("ILS", "USD")] = 1 / 3.2
    asset = make_ils_asset(db_session)
    lot = make_lot(db_session, asset, 10, 350.0, Currency.ILS,
                   purchase_date=dt.datetime.combine(buy, dt.time(10, 0)))
    lot.purchase_date = dt.datetime.combine(buy, dt.time(10, 0))  # naive
    quotes["TEVA.TA"] = ils_quote(350.0)

    v = await PortfolioService(db_session, base_currency=Currency.USD)._value_lot(lot)

    assert v["avgCostBase"] == pytest.approx(100.0)
    assert v["currentPrice"] == pytest.approx(109.375)


@pytest.mark.asyncio
async def test_daily_snapshot_skips_day_without_historical_rate(db_session, rates, quotes):
    day = (NOW - dt.timedelta(days=3)).date()
    rates[("ILS", "USD")] = 0.27  # live rate must NOT be used as a fallback
    asset = make_ils_asset(db_session)
    make_lot(db_session, asset, 10, 60.0, Currency.ILS,
             purchase_date=eod(day) - dt.timedelta(days=10))
    make_snapshot(db_session, asset, 65.0, Currency.ILS, eod(day))

    row = await PortfolioService(db_session, base_currency=Currency.USD).compute_daily_snapshot(day)

    assert row is None
    assert db_session.query(PortfolioSnapshot).count() == 0


@pytest.mark.asyncio
async def test_daily_snapshot_uses_historical_rates(db_session, rates, quotes):
    d1 = (NOW - dt.timedelta(days=4)).date()
    d2 = d1 + dt.timedelta(days=1)
    r1, r2, p1, p2, qty = 4.0, 3.2, 100.0, 110.0, 10
    seed_ils(db_session, d1, r1)
    seed_ils(db_session, d2, r2)
    asset = make_ils_asset(db_session)
    make_lot(db_session, asset, qty, 90.0, Currency.ILS,
             purchase_date=eod(d1) - dt.timedelta(days=10))
    make_snapshot(db_session, asset, p1, Currency.ILS, eod(d1))
    make_snapshot(db_session, asset, p2, Currency.ILS, eod(d2))

    service = PortfolioService(db_session, base_currency=Currency.USD)
    row1 = await service.compute_daily_snapshot(d1)
    row2 = await service.compute_daily_snapshot(d2)

    assert row1.nav == pytest.approx(qty * p1 / r1)
    assert row2.nav == pytest.approx(qty * p2 / r2)
    assert row2.daily_pnl == pytest.approx(qty * (p2 / r2 - p1 / r1))
