"""Scheduler wiring for the daily FX history: refresh calls and the one-time
rebuild of the stored P&L series. Offline: refresh_fx_history is faked and the
portfolio rows use USD assets so no FX is needed."""
import datetime as dt
import uuid

import pytest

from app.models.asset import Asset, AssetType, Currency, MarketRegion
from app.models.holding import HoldingLot
from app.models.maintenance import FX_REBUILD_MARKER, get_marker
from app.models.portfolio_snapshot import PortfolioSnapshot
from app.models.price import FxDailyRate, PriceSnapshot
from app.services.portfolio import PortfolioService
from app.tasks import scheduler

D1 = dt.date(2026, 8, 3)
D2 = dt.date(2026, 8, 4)


def _seed_usd_history(db):
    asset = Asset(
        id=str(uuid.uuid4()), symbol="AAA", name="AAA Inc.", exchange="X",
        currency=Currency.USD, asset_type=AssetType.STOCK,
        market_region=MarketRegion.US,
    )
    db.add(asset)
    db.add(HoldingLot(
        id=str(uuid.uuid4()), asset_id=asset.id, quantity=10, avg_cost=50.0,
        cost_currency=Currency.USD, account_name="m",
        purchase_date=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc),
    ))
    for day, price in ((D1, 100.0), (D2, 110.0)):
        db.add(PriceSnapshot(
            id=str(uuid.uuid4()), asset_id=asset.id, price=price,
            currency=Currency.USD,
            timestamp=dt.datetime.combine(day, dt.time(23, 0), tzinfo=dt.timezone.utc),
            source="eod_snapshot",
        ))
    db.add(PortfolioSnapshot(
        id=str(uuid.uuid4()), date=D1, nav=999999.0, net_flow=0.0,
        daily_pnl=0.0, daily_pnl_pct=0.0, base_currency="USD",
    ))
    db.commit()


def _fake_refresh(db, store_rows):
    calls = []

    async def fake(_db, provider=None, today=None):
        calls.append(1)
        if store_rows:
            _db.add(FxDailyRate(
                id=str(uuid.uuid4()), currency=Currency.ILS, date=D1,
                per_usd=3.5, source="yahoo",
                fetched_at=dt.datetime.now(dt.timezone.utc),
            ))
            _db.commit()
        return 1

    return calls, fake


def _navs(db):
    return {r.date: r.nav for r in db.query(PortfolioSnapshot).all()}


@pytest.mark.asyncio
async def test_job_rebuilds_series_once_and_sets_marker(eval_db, monkeypatch):
    _seed_usd_history(eval_db)
    calls, fake = _fake_refresh(eval_db, store_rows=True)
    monkeypatch.setattr(scheduler, "refresh_fx_history", fake)

    await scheduler.backfill_daily_snapshots_job()

    assert len(calls) == 1
    navs = _navs(eval_db)
    assert navs[D1] == pytest.approx(1000.0)  # stale 999999 replaced
    assert navs[D2] == pytest.approx(1100.0)
    assert get_marker(eval_db, FX_REBUILD_MARKER) is not None

    # Second run: marker present -> rows untouched, no delete.
    row = eval_db.query(PortfolioSnapshot).filter_by(date=D1).one()
    row.nav = 424242.0
    eval_db.commit()
    await scheduler.backfill_daily_snapshots_job()
    assert _navs(eval_db)[D1] == 424242.0
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_job_skips_rebuild_without_fx_rows(eval_db, monkeypatch):
    _seed_usd_history(eval_db)
    calls, fake = _fake_refresh(eval_db, store_rows=False)
    monkeypatch.setattr(scheduler, "refresh_fx_history", fake)

    await scheduler.backfill_daily_snapshots_job()

    assert _navs(eval_db) == {D1: 999999.0}
    assert get_marker(eval_db, FX_REBUILD_MARKER) is None


@pytest.mark.asyncio
async def test_job_survives_refresh_failure(eval_db, monkeypatch):
    _seed_usd_history(eval_db)

    async def boom(*a, **k):
        raise RuntimeError("yahoo down")

    monkeypatch.setattr(scheduler, "refresh_fx_history", boom)
    await scheduler.backfill_daily_snapshots_job()  # must not raise
    assert _navs(eval_db) == {D1: 999999.0}


@pytest.mark.asyncio
async def test_eod_job_refreshes_fx_before_rollup(eval_db, monkeypatch):
    _seed_usd_history(eval_db)
    order = []

    async def fake_refresh(_db, provider=None, today=None):
        order.append("refresh")
        return 0

    async def fake_quote(self, symbol, asset_type):
        return {"price": 120.0, "currency": "USD"}

    async def fake_compute(self, day):
        order.append("compute")

    monkeypatch.setattr(scheduler, "refresh_fx_history", fake_refresh)
    monkeypatch.setattr(scheduler.MarketDataService, "get_quote", fake_quote)
    monkeypatch.setattr(PortfolioService, "compute_daily_snapshot", fake_compute)

    await scheduler.snapshot_eod_prices()

    assert order == ["refresh", "compute"]


# ---- rebuild gate / safe rebuild ----

D0 = dt.date(2026, 7, 1)  # a stale stored day with no price snapshots


def _seed_ils_history(db):
    asset = Asset(
        id=str(uuid.uuid4()), symbol="ILA", name="Ila Ltd.", exchange="TASE",
        currency=Currency.ILS, asset_type=AssetType.STOCK,
        market_region=MarketRegion.IL,
    )
    db.add(asset)
    db.add(HoldingLot(
        id=str(uuid.uuid4()), asset_id=asset.id, quantity=10, avg_cost=350.0,
        cost_currency=Currency.ILS, account_name="m",
        purchase_date=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc),
    ))
    for day, price in ((D1, 350.0), (D2, 385.0)):
        db.add(PriceSnapshot(
            id=str(uuid.uuid4()), asset_id=asset.id, price=price,
            currency=Currency.ILS,
            timestamp=dt.datetime.combine(day, dt.time(23, 0), tzinfo=dt.timezone.utc),
            source="eod_snapshot",
        ))
    for day in (D0, D1):
        db.add(PortfolioSnapshot(
            id=str(uuid.uuid4()), date=day, nav=999999.0, net_flow=0.0,
            daily_pnl=0.0, daily_pnl_pct=0.0, base_currency="USD",
        ))
    db.commit()


def _seed_fx(db, ccy, days, per_usd=3.5):
    for day in days:
        db.add(FxDailyRate(
            id=str(uuid.uuid4()), currency=ccy, date=day, per_usd=per_usd,
            source="yahoo", fetched_at=dt.datetime.now(dt.timezone.utc),
        ))
    db.commit()


def _noop_refresh(monkeypatch):
    async def fake(_db, provider=None, today=None):
        return 0

    monkeypatch.setattr(scheduler, "refresh_fx_history", fake)


@pytest.mark.asyncio
async def test_job_skips_rebuild_when_currency_uncovered(eval_db, monkeypatch, caplog):
    _seed_ils_history(eval_db)
    today = dt.datetime.now(dt.timezone.utc).date()
    _seed_fx(eval_db, Currency.EUR, [D1, today], per_usd=0.9)  # no ILS rows
    _noop_refresh(monkeypatch)

    with caplog.at_level("WARNING"):
        await scheduler.backfill_daily_snapshots_job()

    assert _navs(eval_db) == {D0: 999999.0, D1: 999999.0}
    assert get_marker(eval_db, FX_REBUILD_MARKER) is None
    assert "ILS" in caplog.text


@pytest.mark.asyncio
async def test_job_skips_rebuild_when_coverage_is_stale(eval_db, monkeypatch):
    _seed_ils_history(eval_db)
    _seed_fx(eval_db, Currency.ILS, [D1])  # nothing recent
    _noop_refresh(monkeypatch)

    await scheduler.backfill_daily_snapshots_job()

    assert _navs(eval_db) == {D0: 999999.0, D1: 999999.0}
    assert get_marker(eval_db, FX_REBUILD_MARKER) is None


@pytest.mark.asyncio
async def test_job_rebuilds_with_full_coverage_and_prunes_stale_days(eval_db, monkeypatch):
    _seed_ils_history(eval_db)
    today = dt.datetime.now(dt.timezone.utc).date()
    _seed_fx(eval_db, Currency.ILS, [D1, today])
    _noop_refresh(monkeypatch)

    await scheduler.backfill_daily_snapshots_job()

    navs = _navs(eval_db)
    assert D0 not in navs  # no longer computes -> removed
    assert navs[D1] == pytest.approx(10 * 350.0 / 3.5)  # stale row replaced
    assert D2 in navs
    assert get_marker(eval_db, FX_REBUILD_MARKER) is not None


@pytest.mark.asyncio
async def test_job_keeps_marker_unset_when_nothing_written(eval_db, monkeypatch):
    _seed_usd_history(eval_db)
    _seed_fx(eval_db, Currency.ILS, [D1])
    _noop_refresh(monkeypatch)

    async def none(self, day, usd=None):
        return None

    monkeypatch.setattr(PortfolioService, "compute_daily_snapshot", none)

    await scheduler.backfill_daily_snapshots_job()

    assert _navs(eval_db) == {D1: 999999.0}  # stale row untouched
    assert get_marker(eval_db, FX_REBUILD_MARKER) is None


@pytest.mark.asyncio
async def test_job_error_log_omits_exception_text(eval_db, monkeypatch, caplog):
    _noop_refresh(monkeypatch)

    def boom(self):
        raise RuntimeError("INSERT nav=123456.78")

    monkeypatch.setattr(PortfolioService, "backfill_daily_snapshots", boom)
    _seed_usd_history(eval_db)
    eval_db.query(PortfolioSnapshot).delete()
    eval_db.commit()

    with caplog.at_level("ERROR"):
        await scheduler.backfill_daily_snapshots_job()

    assert "123456.78" not in caplog.text
    assert "RuntimeError" in caplog.text
