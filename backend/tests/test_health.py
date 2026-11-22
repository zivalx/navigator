"""GET /health and GET /health/deep (SQLite, fixed clock)."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.database import get_db
from app.models.alert import AlertRule, PriceAlert
from app.models.asset import Asset, AssetType, Currency, MarketRegion
from app.models.holding import HoldingLot
from app.models.job_heartbeat import JobHeartbeat
from app.models.price import PriceSnapshot
from app.routers import health as health_router
from app.services import health as hs
from app.services.health import Check, JOB_LIMITS, deep_checks, overall_status
from tests.pg_helpers import requires_pg, scratch_schema

# Wednesday 15:00 UTC: US (10:00 NY) and TASE (17:00 Jerusalem) are both in
# session and more than 15 minutes past the open; crypto always.
NOW = datetime(2026, 11, 18, 15, 0, tzinfo=timezone.utc)
SATURDAY = datetime(2026, 11, 21, 15, 0, tzinfo=timezone.utc)
HEAD = "0002"


@pytest.fixture(autouse=True)
def schema_at_head(db_session, monkeypatch):
    monkeypatch.setattr(hs, "head_revision", lambda: HEAD)
    db_session.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
    db_session.execute(text("INSERT INTO alembic_version VALUES (:v)"), {"v": HEAD})
    db_session.commit()


def hb(db, name, started=None, success=None, error=None, error_text=None):
    db.merge(JobHeartbeat(
        job_name=name, last_started_at=started, last_success_at=success,
        last_error_at=error, last_error=error_text,
    ))
    db.commit()


def healthy(db):
    hb(db, "scheduler_leader", started=NOW - timedelta(hours=1), success=NOW - timedelta(seconds=10))
    for name in JOB_LIMITS:
        hb(db, name, started=NOW - timedelta(minutes=1), success=NOW - timedelta(minutes=1))


def asset(db, symbol, atype=AssetType.STOCK, region=MarketRegion.US, currency=Currency.USD):
    a = Asset(id=str(uuid.uuid4()), symbol=symbol, name=symbol, exchange="X",
              currency=currency, asset_type=atype, market_region=region)
    db.add(a)
    db.add(HoldingLot(id=str(uuid.uuid4()), asset_id=a.id, quantity=1, avg_cost=1,
                      cost_currency=currency, account_name="acc", purchase_date=NOW))
    db.commit()
    return a


def snap(db, a, source="yahoo", at=None):
    db.add(PriceSnapshot(id=str(uuid.uuid4()), asset_id=a.id, price=1, currency=Currency.USD,
                         timestamp=at or NOW - timedelta(minutes=5), source=source))
    db.commit()


def us(db):
    return asset(db, "AAA")


def tase(db):
    return asset(db, "BBB.TA", region=MarketRegion.IL, currency=Currency.ILS)


def crypto(db):
    return asset(db, "BTC", atype=AssetType.CRYPTO, region=MarketRegion.US)


def alert(db, a, **kw):
    db.add(PriceAlert(id=str(uuid.uuid4()), asset_id=a.id, rule=AlertRule.PRICE_BELOW,
                      threshold=1, is_active=True, **kw))
    db.commit()


# (check, setup) pairs: each key has one ok and one failing case.
def _db_fail(db, mp):
    def boom(*a, **k):
        raise OperationalError("SELECT 1", {}, Exception("x"))
    mp.setattr(db, "execute", boom)


def _migrations_fail(db, mp):
    db.execute(text("UPDATE alembic_version SET version_num = '0001'"))
    db.commit()


def _leader_stale(db, mp):
    hb(db, "scheduler_leader", started=NOW - timedelta(hours=1), success=NOW - timedelta(minutes=10))


def _leader_lock_lost(db, mp):
    mp.setattr(hs, "leader_lock_held", lambda d: False)


def _job_fail(name):
    def f(db, mp):
        hb(db, name, started=NOW - timedelta(days=2), success=NOW - timedelta(days=2))
    return f


def _alert_ok(db, mp):
    alert(db, us(db), triggered_at=NOW - timedelta(minutes=20), notified_at=NOW - timedelta(minutes=19))


def _alert_fail(db, mp):
    alert(db, us(db), triggered_at=NOW - timedelta(minutes=20))


def _price_ok(make, source="yahoo"):
    def f(db, mp):
        snap(db, make(db), source)
    return f


def _price_fail(make):
    def f(db, mp):
        make(db)
    return f


CASES = [
    ("db", lambda d, m: None, True),
    ("db", _db_fail, False),
    ("migrations", lambda d, m: None, True),
    ("migrations", _migrations_fail, False),
    ("scheduler_leader", lambda d, m: None, True),
    ("scheduler_leader", _leader_stale, False),
    ("scheduler_leader", _leader_lock_lost, False),
    *[(f"job_{n}", lambda d, m: None, True) for n in JOB_LIMITS],
    *[(f"job_{n}", _job_fail(n), False) for n in JOB_LIMITS],
    ("alert_delivery", _alert_ok, True),
    ("alert_delivery", _alert_fail, False),
    ("prices_us", _price_ok(us), True),
    ("prices_us", _price_fail(us), False),
    ("prices_tase", _price_ok(tase), True),
    ("prices_tase", _price_fail(tase), False),
    ("prices_crypto", _price_ok(crypto, "coingecko"), True),
    ("prices_crypto", _price_fail(crypto), False),
]


@pytest.mark.parametrize("name,setup,ok", CASES)
def test_each_check(name, setup, ok, db_session, monkeypatch):
    healthy(db_session)
    setup(db_session, monkeypatch)
    assert deep_checks(db_session, NOW)[name].ok is ok


def test_fresh_install_is_ok(db_session):
    hb(db_session, "scheduler_leader", started=NOW - timedelta(minutes=1))
    assert overall_status(deep_checks(db_session, NOW)) == "ok"


@pytest.fixture()
def client_deep(db_session, monkeypatch):
    monkeypatch.setattr(hs, "_utcnow", lambda: NOW)
    app = FastAPI()
    app.include_router(health_router.router)
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def test_status_mapping_and_503_only_on_down(client_deep, db_session):
    healthy(db_session)
    r = client_deep.get("/health/deep")
    assert r.status_code == 200 and r.json()["status"] == "ok"

    hb(db_session, "breadth_snapshot", started=NOW - timedelta(days=2), success=NOW - timedelta(days=2))
    r = client_deep.get("/health/deep")
    assert r.status_code == 200 and r.json()["status"] == "degraded"
    assert r.json()["checks"]["job_breadth_snapshot"]["ok"] is False

    hb(db_session, "eod_catchup", started=NOW - timedelta(days=2), success=NOW - timedelta(days=2))
    r = client_deep.get("/health/deep")
    assert r.status_code == 503 and r.json()["status"] == "down"


def test_check_names_are_watchdog_safe(client_deep, db_session):
    import re
    healthy(db_session)
    names = client_deep.get("/health/deep").json()["checks"]
    assert names and all(re.fullmatch(r"[a-z0-9_.:-]+", n) for n in names)


def test_prices_closed_outside_session(db_session):
    us(db_session)
    tase(db_session)
    checks = deep_checks(db_session, SATURDAY)
    assert checks["prices_us"] == Check(True, "closed")
    assert checks["prices_tase"] == Check(True, "closed")


def test_prices_closed_in_first_15_min_after_open(db_session):
    us(db_session)
    just_after_open = datetime(2026, 11, 18, 14, 40, tzinfo=timezone.utc)  # 09:40 NY
    assert deep_checks(db_session, just_after_open)["prices_us"] == Check(True, "closed")


def test_never_run_job_uses_leader_start(db_session):
    hb(db_session, "scheduler_leader", started=NOW - timedelta(hours=20))
    assert deep_checks(db_session, NOW)["job_eod_snapshot"].ok
    hb(db_session, "scheduler_leader", started=NOW - timedelta(hours=27))
    assert not deep_checks(db_session, NOW)["job_eod_snapshot"].ok


def test_job_detail_has_ages_only(db_session):
    healthy(db_session)
    hb(db_session, "eod_catchup", started=NOW - timedelta(hours=27), success=NOW - timedelta(hours=27),
       error=NOW - timedelta(minutes=3), error_text="boom")
    c = deep_checks(db_session, NOW)["job_eod_catchup"]
    assert c.detail == "last success 27h ago (limit 26h); last error 3m ago"


def test_prices_crypto_degraded_when_only_yahoo_fresh(db_session):
    healthy(db_session)
    snap(db_session, crypto(db_session), "yahoo")
    checks = deep_checks(db_session, NOW)
    c = checks["prices_crypto"]
    assert not c.ok and not c.critical
    assert overall_status(checks) == "degraded"


def test_prices_crypto_down_when_neither_source_fresh(db_session):
    healthy(db_session)
    a = crypto(db_session)
    snap(db_session, a, "yahoo", at=NOW - timedelta(hours=1))
    c = deep_checks(db_session, NOW)["prices_crypto"]
    assert not c.ok and c.critical


def test_not_monitored_never_fails(db_session):
    healthy(db_session)
    asset(db_session, "FUND", atype=AssetType.FUND)
    c = deep_checks(db_session, NOW)["prices_us"]
    assert c.ok and "1 not_monitored" in c.detail


def test_price_detail_counts(db_session):
    healthy(db_session)
    a = asset(db_session, "A1")
    asset(db_session, "A2")
    snap(db_session, a)
    asset(db_session, "FUND", atype=AssetType.FUND)
    c = deep_checks(db_session, NOW)["prices_us"]
    assert c.detail == "1 of 2 watched assets without a fresh price for 30+ min; 1 not_monitored"


def test_db_unreachable_skips_rest(db_session, monkeypatch):
    _db_fail(db_session, monkeypatch)
    checks = deep_checks(db_session, NOW)
    assert not checks["db"].ok
    assert all(v.detail == "skipped: db unreachable" for k, v in checks.items() if k != "db")
    assert overall_status(checks) == "down"


def test_naive_times_treated_as_utc(db_session):
    naive = NOW.replace(tzinfo=None)
    hb(db_session, "scheduler_leader", started=naive - timedelta(hours=1), success=naive - timedelta(seconds=10))
    for name in JOB_LIMITS:
        hb(db_session, name, started=naive - timedelta(minutes=1), success=naive - timedelta(minutes=1))
    a = us(db_session)
    snap(db_session, a, at=naive - timedelta(minutes=5))
    alert(db_session, a, triggered_at=naive - timedelta(minutes=20))
    checks = deep_checks(db_session, NOW)
    assert checks["scheduler_leader"].detail == "leader heartbeat 10s ago"
    assert checks["prices_us"].ok
    assert not checks["alert_delivery"].ok


def test_body_has_no_quantity_cost_symbol_or_error_text(client_deep, db_session):
    healthy(db_session)
    a = Asset(id=str(uuid.uuid4()), symbol="ZQXJ", name="n", exchange="X", currency=Currency.USD,
              asset_type=AssetType.STOCK, market_region=MarketRegion.US)
    db_session.add(a)
    db_session.add(HoldingLot(id=str(uuid.uuid4()), asset_id=a.id, quantity=123.456, avg_cost=789.01,
                              cost_currency=Currency.USD, account_name="acc", purchase_date=NOW))
    db_session.commit()
    hb(db_session, "quote_refresh", started=NOW - timedelta(days=2), success=NOW - timedelta(days=2),
       error=NOW - timedelta(minutes=1), error_text="ZQXJ boom")
    resp = client_deep.get("/health/deep")
    assert resp.status_code == 503
    assert not any(s in resp.text for s in ("123.456", "789.01", "ZQXJ", "boom"))


def test_shallow_health_needs_no_db(monkeypatch):
    from app import database
    from app.main import app

    def boom(*a, **k):
        raise RuntimeError("no db")

    monkeypatch.setattr(database, "SessionLocal", boom)
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    assert r.json()["checks"]["process"] == {"ok": True, "detail": "up"}
    assert r.json()["status"] == "ok"


@requires_pg
def test_pg_leader_lock_held_and_statement_timeout():
    from sqlalchemy.orm import Session

    from app.locks import SCHEDULER_LOCK_KEY, advisory_lock

    with scratch_schema() as engine:
        with Session(engine) as db:
            assert hs.leader_lock_held(db) is False
            with advisory_lock(engine, SCHEDULER_LOCK_KEY):
                assert hs.leader_lock_held(db) is True
            db.rollback()
            assert hs.leader_lock_held(db) is False
            db.execute(text("SET LOCAL statement_timeout = '2s'"))
            assert db.execute(text("SHOW statement_timeout")).scalar() == "2s"


def test_budget_cuts_off_remaining_checks(db_session, monkeypatch):
    healthy(db_session)
    ticks = iter([0.0, 0.0, 0.0, 100.0] + [100.0] * 100)  # budget (8 s) spent after two checks
    monkeypatch.setattr(hs, "_monotonic", lambda: next(ticks))
    checks = deep_checks(db_session, NOW)
    assert checks["db"].ok and checks["migrations"].ok
    for k in ("scheduler_leader", "job_quote_refresh", "alert_delivery", "prices_us", "prices_crypto"):
        assert checks[k] == Check(False, "check failed", checks[k].critical)
    assert checks["job_breadth_snapshot"].critical is False
    assert overall_status(checks) == "down"


def test_exception_path_keeps_error_text_out_of_body_and_logs(client_deep, db_session, monkeypatch, caplog):
    healthy(db_session)

    def boom(db, now):
        raise RuntimeError("ZQXJ postgresql://user:pw@dbhost/x?token=sekrit")

    monkeypatch.setattr(hs, "_check_alert_delivery", boom)
    resp = client_deep.get("/health/deep")
    assert resp.status_code == 503
    assert resp.json()["checks"]["alert_delivery"] == {"ok": False, "detail": "check failed"}
    assert not any(s in resp.text for s in ("ZQXJ", "sekrit", "dbhost"))
    assert "sekrit" not in caplog.text and all(r.exc_info is None for r in caplog.records)


def test_us_holiday_with_fresh_snapshots_is_ok(db_session):
    healthy(db_session)
    thanksgiving = datetime(2026, 11, 26, 15, 0, tzinfo=timezone.utc)
    snap(db_session, us(db_session), at=thanksgiving - timedelta(minutes=5))
    hb(db_session, "scheduler_leader", started=thanksgiving - timedelta(hours=1),
       success=thanksgiving - timedelta(seconds=10))
    for name in JOB_LIMITS:
        hb(db_session, name, started=thanksgiving - timedelta(minutes=1), success=thanksgiving - timedelta(minutes=1))
    assert overall_status(deep_checks(db_session, thanksgiving)) == "ok"


def test_funds_only_portfolio_is_ok(db_session):
    healthy(db_session)
    asset(db_session, "FUND", atype=AssetType.FUND)
    checks = deep_checks(db_session, NOW)
    assert overall_status(checks) == "ok"
    assert checks["prices_us"].detail == "no monitored assets; 1 not_monitored"


def test_tase_closed_while_us_open(db_session):
    healthy(db_session)
    friday = datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)  # TASE closed 14:00 local, US open
    snap(db_session, us(db_session), at=friday - timedelta(minutes=5))
    tase(db_session)
    hb(db_session, "scheduler_leader", started=friday - timedelta(hours=1), success=friday - timedelta(seconds=10))
    for name in JOB_LIMITS:
        hb(db_session, name, started=friday - timedelta(minutes=1), success=friday - timedelta(minutes=1))
    checks = deep_checks(db_session, friday)
    assert checks["prices_tase"] == Check(True, "closed")
    assert checks["prices_us"].ok and overall_status(checks) == "ok"


def test_leader_restart_after_outage_is_ok(db_session):
    hb(db_session, "scheduler_leader", started=NOW - timedelta(seconds=5), success=NOW - timedelta(hours=3))
    assert deep_checks(db_session, NOW)["scheduler_leader"].ok


@requires_pg
def test_pg_statement_timeout_survives_rollback(db_session):
    from sqlalchemy.orm import Session

    with scratch_schema() as engine:
        with Session(engine) as db:
            hs.arm_statement_timeout(db)
            assert db.execute(text("SHOW statement_timeout")).scalar() == "2s"
            hs._rollback(db)
            assert db.execute(text("SHOW statement_timeout")).scalar() == "2s"
            hs._guard(lambda: 1 / 0, db)
            assert db.execute(text("SHOW statement_timeout")).scalar() == "2s"


@requires_pg
def test_pg_non_leader_worker_reads_lock_from_pg_locks():
    from sqlalchemy.orm import Session

    from app.locks import SCHEDULER_LOCK_KEY, advisory_lock

    with scratch_schema() as engine:
        JobHeartbeat.__table__.create(engine)
        with Session(engine) as db:
            db.add(JobHeartbeat(job_name="scheduler_leader", last_success_at=NOW))
            db.commit()
            hbs = {"scheduler_leader": hs._Hb(None, NOW, None)}
            assert hs._check_leader(db, NOW, hbs) == Check(False, "no leader lock held")
            with advisory_lock(engine, SCHEDULER_LOCK_KEY):  # another process holds it
                assert hs._check_leader(db, NOW, hbs).ok
