"""Tests for the price-alerts feature.

Covers docs/superpowers/specs/2026-07-23-price-alerts-design.md:
- CRUD + ?status= filters on /api/alerts
- symbol-based creation (resolve/create asset)
- acknowledge + reactivate flows
- the APScheduler evaluation job: below/above rules, inclusive boundary,
  already-inactive alerts skipped, and quote-fetch failure leaving alerts
  untouched.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models.alert import AlertRule, PriceAlert
from app.models.asset import Asset, AssetType, Currency, MarketRegion
from app.routers import alerts as alerts_router_module
from app.services.market_data import MarketDataService
from app.tasks import scheduler as scheduler_module

NOW = datetime.now(timezone.utc)


@pytest.fixture()
def db_session():
    """Module-local override of conftest's db_session fixture.

    Uses StaticPool (single shared connection) instead of the default
    per-thread SQLite pool: this module drives requests through
    TestClient, whose ASGI calls can run in a worker thread, and the
    stock in-memory-sqlite fixture would otherwise hand that thread a
    brand-new (tableless) :memory: database.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)

    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


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


def make_alert(
    db,
    asset,
    rule=AlertRule.PRICE_BELOW,
    threshold=100.0,
    is_active=True,
    triggered_at=None,
    triggered_price=None,
    acknowledged_at=None,
    note=None,
):
    alert = PriceAlert(
        id=str(uuid.uuid4()),
        asset_id=asset.id,
        rule=rule,
        threshold=threshold,
        note=note,
        is_active=is_active,
        triggered_at=triggered_at,
        triggered_price=triggered_price,
        acknowledged_at=acknowledged_at,
    )
    db.add(alert)
    db.commit()
    return alert


@pytest.fixture()
def client(db_session):
    app = FastAPI()
    app.include_router(alerts_router_module.router, prefix="/api/alerts")
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


# ---------------------------------------------------------------------------
# CRUD + status filters
# ---------------------------------------------------------------------------

def test_create_alert_by_asset_id(client, db_session):
    asset = make_asset(db_session, symbol="AAPL")

    response = client.post(
        "/api/alerts/",
        json={"assetId": asset.id, "rule": "price_below", "threshold": 180.0, "note": "stop loss"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["assetId"] == asset.id
    assert body["symbol"] == "AAPL"
    assert body["name"] == "AAPL Inc."
    assert body["rule"] == "price_below"
    assert body["threshold"] == 180.0
    assert body["note"] == "stop loss"
    assert body["isActive"] is True
    assert body["triggeredAt"] is None
    assert body["triggeredPrice"] is None
    assert body["acknowledgedAt"] is None

    # Persisted.
    stored = db_session.query(PriceAlert).filter(PriceAlert.id == body["id"]).first()
    assert stored is not None
    assert stored.threshold == 180.0


def test_create_alert_requires_asset_id_or_symbol(client):
    response = client.post(
        "/api/alerts/",
        json={"rule": "price_below", "threshold": 100.0},
    )
    assert response.status_code == 422


def test_create_alert_rejects_nonpositive_threshold(client, db_session):
    asset = make_asset(db_session, symbol="AAPL")
    response = client.post(
        "/api/alerts/",
        json={"assetId": asset.id, "rule": "price_below", "threshold": 0},
    )
    assert response.status_code == 400


def test_create_alert_unknown_asset_id_404s(client):
    response = client.post(
        "/api/alerts/",
        json={"assetId": "does-not-exist", "rule": "price_below", "threshold": 100.0},
    )
    assert response.status_code == 404


def test_get_alerts_status_filters(client, db_session):
    asset = make_asset(db_session, symbol="AAPL")
    active = make_alert(db_session, asset, threshold=100.0)
    triggered_unack = make_alert(
        db_session, asset, threshold=90.0, is_active=False,
        triggered_at=NOW, triggered_price=89.0,
    )
    triggered_ack = make_alert(
        db_session, asset, threshold=80.0, is_active=False,
        triggered_at=NOW, triggered_price=79.0, acknowledged_at=NOW,
    )

    all_ids = {a["id"] for a in client.get("/api/alerts/").json()}
    assert all_ids == {active.id, triggered_unack.id, triggered_ack.id}

    active_ids = {a["id"] for a in client.get("/api/alerts/", params={"status": "active"}).json()}
    assert active_ids == {active.id}

    triggered_ids = {a["id"] for a in client.get("/api/alerts/", params={"status": "triggered"}).json()}
    assert triggered_ids == {triggered_unack.id, triggered_ack.id}

    unack_ids = {a["id"] for a in client.get("/api/alerts/", params={"status": "unacknowledged"}).json()}
    assert unack_ids == {triggered_unack.id}


def test_get_alerts_invalid_status_400s(client):
    response = client.get("/api/alerts/", params={"status": "bogus"})
    assert response.status_code == 400


def test_update_alert_edits_fields(client, db_session):
    asset = make_asset(db_session, symbol="AAPL")
    alert = make_alert(db_session, asset, threshold=100.0, note="old note")

    response = client.put(
        f"/api/alerts/{alert.id}",
        json={"threshold": 150.0, "note": "new note", "rule": "price_above"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["threshold"] == 150.0
    assert body["note"] == "new note"
    assert body["rule"] == "price_above"


def test_update_alert_no_fields_400s(client, db_session):
    asset = make_asset(db_session, symbol="AAPL")
    alert = make_alert(db_session, asset)
    response = client.put(f"/api/alerts/{alert.id}", json={})
    assert response.status_code == 400


def test_update_alert_not_found_404s(client):
    response = client.put("/api/alerts/does-not-exist", json={"threshold": 1.0})
    assert response.status_code == 404


def test_delete_alert(client, db_session):
    asset = make_asset(db_session, symbol="AAPL")
    alert = make_alert(db_session, asset)

    response = client.delete(f"/api/alerts/{alert.id}")
    assert response.status_code == 204
    assert db_session.query(PriceAlert).filter(PriceAlert.id == alert.id).first() is None

    # Deleting again 404s.
    assert client.delete(f"/api/alerts/{alert.id}").status_code == 404


# ---------------------------------------------------------------------------
# Symbol-based creation
# ---------------------------------------------------------------------------

def test_create_alert_by_existing_symbol(client, db_session):
    asset = make_asset(db_session, symbol="MSFT")

    response = client.post(
        "/api/alerts/",
        json={"symbol": "msft", "rule": "price_above", "threshold": 500.0},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["assetId"] == asset.id
    assert body["symbol"] == "MSFT"

    # No duplicate asset created.
    assert db_session.query(Asset).filter(Asset.symbol == "MSFT").count() == 1


def test_create_alert_by_new_symbol_creates_asset(client, db_session, monkeypatch):
    async def fake_get_quote(self, symbol, asset_type=None):
        return {"symbol": symbol.upper(), "price": 42.0, "currency": "USD"}

    monkeypatch.setattr(MarketDataService, "get_quote", fake_get_quote)

    assert db_session.query(Asset).filter(Asset.symbol == "TSLA").first() is None

    response = client.post(
        "/api/alerts/",
        json={"symbol": "tsla", "rule": "price_below", "threshold": 200.0},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["symbol"] == "TSLA"

    created = db_session.query(Asset).filter(Asset.symbol == "TSLA").first()
    assert created is not None
    assert created.currency == Currency.USD


def test_create_alert_by_new_symbol_when_quote_fetch_fails_still_creates_asset(
    client, db_session, monkeypatch
):
    async def fake_get_quote(self, symbol, asset_type=None):
        raise ValueError("no data")

    monkeypatch.setattr(MarketDataService, "get_quote", fake_get_quote)

    response = client.post(
        "/api/alerts/",
        json={"symbol": "zzzz", "rule": "price_below", "threshold": 10.0},
    )
    assert response.status_code == 201
    assert response.json()["symbol"] == "ZZZZ"


def test_create_alert_empty_symbol_400s(client):
    response = client.post(
        "/api/alerts/",
        json={"symbol": "   ", "rule": "price_below", "threshold": 10.0},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Acknowledge + reactivate flows
# ---------------------------------------------------------------------------

def test_acknowledge_sets_acknowledged_at(client, db_session):
    asset = make_asset(db_session, symbol="AAPL")
    alert = make_alert(
        db_session, asset, is_active=False, triggered_at=NOW, triggered_price=89.0,
    )

    response = client.post(f"/api/alerts/{alert.id}/acknowledge")
    assert response.status_code == 200
    body = response.json()
    assert body["acknowledgedAt"] is not None

    db_session.refresh(alert)
    assert alert.acknowledged_at is not None


def test_acknowledge_is_idempotent(client, db_session):
    asset = make_asset(db_session, symbol="AAPL")
    alert = make_alert(
        db_session, asset, is_active=False, triggered_at=NOW, triggered_price=89.0,
        acknowledged_at=NOW - timedelta(minutes=5),
    )
    original_ack = alert.acknowledged_at

    response = client.post(f"/api/alerts/{alert.id}/acknowledge")
    assert response.status_code == 200

    db_session.refresh(alert)
    assert alert.acknowledged_at == original_ack


def test_acknowledge_not_found_404s(client):
    response = client.post("/api/alerts/does-not-exist/acknowledge")
    assert response.status_code == 404


def test_reactivate_clears_triggered_and_acknowledged_fields(client, db_session):
    asset = make_asset(db_session, symbol="AAPL")
    alert = make_alert(
        db_session, asset, is_active=False,
        triggered_at=NOW, triggered_price=89.0, acknowledged_at=NOW,
    )

    response = client.put(f"/api/alerts/{alert.id}", json={"isActive": True})
    assert response.status_code == 200
    body = response.json()
    assert body["isActive"] is True
    assert body["triggeredAt"] is None
    assert body["triggeredPrice"] is None
    assert body["acknowledgedAt"] is None

    db_session.refresh(alert)
    assert alert.is_active is True
    assert alert.triggered_at is None
    assert alert.triggered_price is None
    assert alert.acknowledged_at is None


# ---------------------------------------------------------------------------
# Scheduler evaluation job
# ---------------------------------------------------------------------------

@pytest.fixture()
def eval_db(db_session, monkeypatch):
    """Point the scheduler's SessionLocal() calls at the shared test session
    so evaluate_price_alerts() operates on the same in-memory DB.

    evaluate_price_alerts() calls db.close() in its own finally block (as it
    does in production, where it owns the session end-to-end) — that expunges
    objects from the identity map, so tests re-query by id afterwards instead
    of calling session.refresh() on the original instances.
    """
    monkeypatch.setattr(scheduler_module, "SessionLocal", lambda: db_session)
    return db_session


def reload(db, alert_id):
    """Re-query an alert by id.

    evaluate_price_alerts() closes its session when done, which detaches
    (and expires) the ORM instances created via make_alert() in these
    tests — so accessing `.id` on the original object after the call would
    itself raise. Capture the id string *before* invoking evaluate, then
    reload() to inspect post-evaluation state.
    """
    return db.query(PriceAlert).filter(PriceAlert.id == alert_id).first()


def _patch_quotes(monkeypatch, quotes_by_symbol):
    async def fake_get_quotes(self, symbols):
        return {s: quotes_by_symbol[s] for s in symbols if s in quotes_by_symbol}

    monkeypatch.setattr(MarketDataService, "get_quotes", fake_get_quotes)


@pytest.mark.asyncio
async def test_evaluate_triggers_price_below_when_price_falls_below_threshold(eval_db, monkeypatch):
    asset = make_asset(eval_db, symbol="AAPL")
    alert = make_alert(eval_db, asset, rule=AlertRule.PRICE_BELOW, threshold=180.0)
    alert_id = alert.id
    _patch_quotes(monkeypatch, {"AAPL": {"price": 175.0}})

    await scheduler_module.evaluate_price_alerts()

    alert = reload(eval_db, alert_id)
    assert alert.is_active is False
    assert alert.triggered_price == 175.0
    assert alert.triggered_at is not None


@pytest.mark.asyncio
async def test_evaluate_triggers_price_above_when_price_rises_above_threshold(eval_db, monkeypatch):
    asset = make_asset(eval_db, symbol="AAPL")
    alert = make_alert(eval_db, asset, rule=AlertRule.PRICE_ABOVE, threshold=200.0)
    alert_id = alert.id
    _patch_quotes(monkeypatch, {"AAPL": {"price": 205.0}})

    await scheduler_module.evaluate_price_alerts()

    alert = reload(eval_db, alert_id)
    assert alert.is_active is False
    assert alert.triggered_price == 205.0


@pytest.mark.asyncio
async def test_evaluate_boundary_is_inclusive(eval_db, monkeypatch):
    asset = make_asset(eval_db, symbol="AAPL")
    below = make_alert(eval_db, asset, rule=AlertRule.PRICE_BELOW, threshold=180.0)
    above = make_alert(eval_db, asset, rule=AlertRule.PRICE_ABOVE, threshold=180.0)
    below_id, above_id = below.id, above.id
    _patch_quotes(monkeypatch, {"AAPL": {"price": 180.0}})

    await scheduler_module.evaluate_price_alerts()

    assert reload(eval_db, below_id).is_active is False
    assert reload(eval_db, above_id).is_active is False


@pytest.mark.asyncio
async def test_evaluate_does_not_trigger_when_condition_not_met(eval_db, monkeypatch):
    asset = make_asset(eval_db, symbol="AAPL")
    below = make_alert(eval_db, asset, rule=AlertRule.PRICE_BELOW, threshold=180.0)
    above = make_alert(eval_db, asset, rule=AlertRule.PRICE_ABOVE, threshold=200.0)
    below_id, above_id = below.id, above.id
    _patch_quotes(monkeypatch, {"AAPL": {"price": 190.0}})

    await scheduler_module.evaluate_price_alerts()

    below = reload(eval_db, below_id)
    above = reload(eval_db, above_id)
    assert below.is_active is True
    assert below.triggered_at is None
    assert above.is_active is True
    assert above.triggered_at is None


@pytest.mark.asyncio
async def test_evaluate_skips_already_inactive_alerts(eval_db, monkeypatch):
    asset = make_asset(eval_db, symbol="AAPL")
    already_triggered = make_alert(
        eval_db, asset, rule=AlertRule.PRICE_BELOW, threshold=180.0,
        is_active=False, triggered_at=NOW - timedelta(hours=1), triggered_price=175.0,
    )
    already_triggered_id = already_triggered.id
    _patch_quotes(monkeypatch, {"AAPL": {"price": 100.0}})

    await scheduler_module.evaluate_price_alerts()

    already_triggered = reload(eval_db, already_triggered_id)
    # Untouched - triggered_price/at from before, not overwritten.
    # (SQLite drops tzinfo on round-trip, so compare naive wall-clock values.)
    assert already_triggered.triggered_price == 175.0
    assert already_triggered.triggered_at.replace(tzinfo=None) == (
        NOW - timedelta(hours=1)
    ).replace(tzinfo=None)


@pytest.mark.asyncio
async def test_evaluate_leaves_alerts_untouched_when_quote_fetch_fails(eval_db, monkeypatch):
    asset = make_asset(eval_db, symbol="AAPL")
    alert = make_alert(eval_db, asset, rule=AlertRule.PRICE_BELOW, threshold=180.0)
    alert_id = alert.id

    async def failing_get_quotes(self, symbols):
        raise RuntimeError("provider outage")

    monkeypatch.setattr(MarketDataService, "get_quotes", failing_get_quotes)

    await scheduler_module.evaluate_price_alerts()

    alert = reload(eval_db, alert_id)
    assert alert.is_active is True
    assert alert.triggered_at is None
    assert alert.triggered_price is None


@pytest.mark.asyncio
async def test_evaluate_skips_alert_when_its_own_quote_has_error(eval_db, monkeypatch):
    asset = make_asset(eval_db, symbol="AAPL")
    alert = make_alert(eval_db, asset, rule=AlertRule.PRICE_BELOW, threshold=180.0)
    alert_id = alert.id
    _patch_quotes(monkeypatch, {"AAPL": {"error": "no data"}})

    await scheduler_module.evaluate_price_alerts()

    alert = reload(eval_db, alert_id)
    assert alert.is_active is True


@pytest.mark.asyncio
async def test_evaluate_noop_when_no_active_alerts(eval_db, monkeypatch):
    _patch_quotes(monkeypatch, {})
    # Should not raise even with nothing to evaluate.
    await scheduler_module.evaluate_price_alerts()
