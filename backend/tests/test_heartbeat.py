"""@heartbeat wrapper and the jobs that raise to record failure."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.asset import Currency
from app.models.holding import HoldingLot
from app.models.job_heartbeat import JobHeartbeat
from app.models.price import PriceSnapshot
from app.services import breadth as breadth_service
from app.services.market_data import MarketDataService
from app.tasks import heartbeat as heartbeat_module
from app.tasks import leader
from app.tasks import scheduler as scheduler_module
from app.tasks.heartbeat import heartbeat
from app.tasks.scheduler import build_scheduler
from tests.factories import _with_alertable_defaults, make_asset


@pytest.fixture()
def hb_db(db_session, monkeypatch):
    """Heartbeat writes and the job use separate sessions on one engine."""
    monkeypatch.setattr(heartbeat_module, "SessionLocal", sessionmaker(bind=db_session.get_bind()))
    return db_session


@pytest.fixture()
def as_leader(monkeypatch):
    monkeypatch.setattr(leader, "is_leader", lambda: True)


def _row(db, name="job"):
    db.expire_all()
    return db.get(JobHeartbeat, name)


@pytest.mark.asyncio
async def test_success_stamps_last_success_at(hb_db, as_leader):
    async def job():
        return None

    await heartbeat("job")(job)()
    row = _row(hb_db)
    assert row.last_started_at and row.last_success_at and row.last_error_at is None


@pytest.mark.asyncio
async def test_error_is_redacted_and_truncated(hb_db, as_leader):
    async def job():
        raise ValueError("token=SECRETKEY " + "x" * 1000)

    await heartbeat("job")(job)()  # swallowed
    row = _row(hb_db)
    assert "SECRETKEY" not in row.last_error and "<redacted>" in row.last_error
    assert len(row.last_error) <= 500
    assert row.last_error_at and row.last_started_at and row.last_success_at is None


@pytest.mark.asyncio
async def test_job_rollback_keeps_heartbeat(hb_db, as_leader):
    Session = sessionmaker(bind=hb_db.get_bind())

    async def job():
        db = Session()
        db.add(make_row())
        db.flush()
        db.rollback()
        db.close()
        raise RuntimeError("boom")

    def make_row():
        from app.models.asset import Asset, AssetType, MarketRegion
        return Asset(id=str(uuid.uuid4()), symbol="RB", name="RB", exchange="X",
                     currency=Currency.USD, asset_type=AssetType.STOCK, market_region=MarketRegion.US)

    await heartbeat("job")(job)()
    row = _row(hb_db)
    assert row.last_started_at and row.last_error_at
    from app.models.asset import Asset
    assert hb_db.query(Asset).count() == 0


@pytest.mark.asyncio
async def test_non_leader_returns_without_running(hb_db, monkeypatch):
    monkeypatch.setattr(leader, "is_leader", lambda: False)
    calls = []

    async def job():
        calls.append(1)

    await heartbeat("job")(job)()
    assert calls == [] and _row(hb_db) is None


@pytest.mark.asyncio
async def test_heartbeat_write_failure_neither_blocks_nor_raises(monkeypatch, as_leader):
    def broken(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(heartbeat_module, "upsert_heartbeat", broken)
    calls = []

    async def job():
        calls.append(1)

    await heartbeat("job")(job)()
    assert calls == [1]


def test_wrapper_keeps_job_name_and_marks_heartbeat_name():
    async def my_job():
        return None

    wrapped = heartbeat("x")(my_job)
    assert wrapped.__heartbeat_name__ == "x" and wrapped.__name__ == "my_job"


# --- jobs raise to fail ----------------------------------------------------

@pytest.mark.asyncio
async def test_evaluate_raises_when_quote_fetch_fails(eval_db, monkeypatch):
    from app.models.alert import AlertRule
    from tests.factories import make_alert, reload_alert

    asset = make_asset(eval_db, symbol="AAPL")
    alert_id = make_alert(eval_db, asset, rule=AlertRule.PRICE_BELOW, threshold=180.0).id
    notified = []

    async def failing(self, symbols):
        raise RuntimeError("provider outage")

    async def fake_notify(db):
        notified.append(1)

    monkeypatch.setattr(MarketDataService, "get_quotes", failing)
    monkeypatch.setattr(scheduler_module, "notify_triggered_alerts", fake_notify)
    with pytest.raises(RuntimeError, match="provider outage"):
        await scheduler_module.evaluate_price_alerts()
    assert notified == [1]  # the notify pass still ran first
    alert = reload_alert(eval_db, alert_id)
    assert alert.is_active is True and alert.triggered_at is None


def _hold(db, asset):
    db.add(HoldingLot(id=str(uuid.uuid4()), asset_id=asset.id, quantity=1, avg_cost=1.0,
                      cost_currency=Currency.USD, account_name="a",
                      purchase_date=datetime.now(timezone.utc) - timedelta(days=5)))
    db.commit()


@pytest.mark.asyncio
async def test_eod_snapshot_raises_when_an_asset_fails(eval_db, monkeypatch):
    good, bad = make_asset(eval_db, symbol="GOOD"), make_asset(eval_db, symbol="BAD")
    _hold(eval_db, good)
    _hold(eval_db, bad)
    good_id = good.id

    async def get_quote(self, symbol, asset_type=None):
        if symbol == "BAD":
            raise RuntimeError("no quote")
        return _with_alertable_defaults({"price": 10.0, "currency": "USD"})

    async def no_portfolio(self, day):
        return None

    monkeypatch.setattr(MarketDataService, "get_quote", get_quote)
    monkeypatch.setattr(scheduler_module.PortfolioService, "compute_daily_snapshot", no_portfolio)
    with pytest.raises(RuntimeError, match="1 asset"):
        await scheduler_module.snapshot_eod_prices()
    eval_db.expire_all()
    rows = eval_db.query(PriceSnapshot).filter_by(source="eod_snapshot").all()
    assert [r.asset_id for r in rows] == [good_id]


@pytest.mark.asyncio
async def test_eod_snapshot_raises_when_portfolio_row_fails(eval_db, monkeypatch):
    asset = make_asset(eval_db, symbol="GOOD")
    _hold(eval_db, asset)

    async def get_quote(self, symbol, asset_type=None):
        return _with_alertable_defaults({"price": 10.0, "currency": "USD"})

    async def failing_portfolio(self, day):
        raise RuntimeError("fx missing")

    monkeypatch.setattr(MarketDataService, "get_quote", get_quote)
    monkeypatch.setattr(scheduler_module.PortfolioService, "compute_daily_snapshot", failing_portfolio)
    with pytest.raises(RuntimeError, match="daily portfolio row failed"):
        await scheduler_module.snapshot_eod_prices()


@pytest.mark.asyncio
async def test_breadth_raises_when_no_data(monkeypatch):
    import pandas as pd

    async def constituents():
        return ["AAA", "BBB"]

    monkeypatch.setattr(breadth_service, "fetch_sp500_constituents", constituents)
    monkeypatch.setattr(breadth_service, "_download_chunk_closes", lambda syms: pd.DataFrame())
    with pytest.raises(RuntimeError, match="no price data"):
        await breadth_service.compute_and_store_breadth()


@pytest.mark.asyncio
async def test_breadth_raises_when_no_constituents(monkeypatch):
    async def constituents():
        return []

    monkeypatch.setattr(breadth_service, "fetch_sp500_constituents", constituents)
    with pytest.raises(RuntimeError, match="no constituents"):
        await breadth_service.compute_and_store_breadth()


# --- registration ----------------------------------------------------------

def test_job_ids_equal_heartbeat_names_and_every_job_is_guarded():
    jobs = build_scheduler(startup_at=datetime.now(timezone.utc) + timedelta(seconds=15)).get_jobs()
    assert {j.id for j in jobs} >= {"eod_snapshot", "breadth_snapshot", "price_alert_evaluation", "leader_startup"}
    for j in jobs:  # includes leader_startup: no registered job escapes the guard
        assert getattr(j.func, "__heartbeat_name__", None) == j.id, j.id


@pytest.mark.asyncio
async def test_registered_jobs_do_not_run_when_not_leader(hb_db, monkeypatch):
    monkeypatch.setattr(leader, "is_leader", lambda: False)
    sched = build_scheduler(startup_at=datetime.now(timezone.utc) + timedelta(seconds=15))

    def boom(*a, **k):
        raise AssertionError("job body ran on a non-leader")

    monkeypatch.setattr(scheduler_module, "SessionLocal", boom)
    monkeypatch.setattr(breadth_service, "fetch_sp500_constituents", boom)
    for j in sched.get_jobs():
        await j.func()
    assert hb_db.query(JobHeartbeat).count() == 0
