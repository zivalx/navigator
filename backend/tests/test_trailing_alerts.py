"""Tests for trailing-stop alerts, buy/sell intents, and Telegram notification.

Covers docs/superpowers/specs/2026-08-12-trailing-stop-alerts-design.md:
- create/update validation (trail XOR, threshold rules, intent defaulting)
- high-water-mark seeding on create and re-seeding on reactivate
- the evaluation job: ratchet up (never down), %- and $-trail triggering with
  inclusive boundary, legacy alerts without a mark
- the notification pass: send-once, retry-after-failure, disabled-channel
  handling, and message formatting.
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
from app.models.alert import AlertIntent, AlertRule, PriceAlert
from app.models.asset import Asset, AssetType, Currency, MarketRegion
from app.routers import alerts as alerts_router_module
from app.services import notifications as notifications_module
from app.services.market_data import MarketDataService
from app.services.notifications import format_alert_message
from app.tasks import scheduler as scheduler_module

NOW = datetime.now(timezone.utc)


@pytest.fixture()
def db_session():
    """StaticPool in-memory SQLite (same rationale as test_alerts.py)."""
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


@pytest.fixture()
def client(db_session):
    app = FastAPI()
    app.include_router(alerts_router_module.router, prefix="/api/alerts")
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


@pytest.fixture()
def eval_db(db_session, monkeypatch):
    monkeypatch.setattr(scheduler_module, "SessionLocal", lambda: db_session)
    return db_session


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


def make_tsl(db, asset, trail_percent=None, trail_amount=None, hwm=100.0, **kwargs):
    alert = PriceAlert(
        id=str(uuid.uuid4()),
        asset_id=asset.id,
        rule=AlertRule.TRAILING_STOP,
        intent=AlertIntent.SELL,
        trail_percent=trail_percent,
        trail_amount=trail_amount,
        high_water_mark=hwm,
        is_active=kwargs.pop("is_active", True),
        **kwargs,
    )
    db.add(alert)
    db.commit()
    return alert


def reload(db, alert_id):
    return db.query(PriceAlert).filter(PriceAlert.id == alert_id).first()


def _patch_quote(monkeypatch, price):
    async def fake_get_quote(self, symbol, asset_type=None):
        return {"symbol": symbol.upper(), "price": price, "currency": "USD"}

    monkeypatch.setattr(MarketDataService, "get_quote", fake_get_quote)


def _patch_quotes(monkeypatch, quotes_by_symbol):
    async def fake_get_quotes(self, symbols):
        return {s: quotes_by_symbol[s] for s in symbols if s in quotes_by_symbol}

    monkeypatch.setattr(MarketDataService, "get_quotes", fake_get_quotes)


class FakeNotificationService:
    """Records messages; success/failure scripted per instance-shared list."""

    enabled = True
    should_succeed = True
    sent: list = []

    async def send(self, text: str) -> bool:
        if not self.enabled:
            return False
        type(self).sent.append(text)
        return self.should_succeed


@pytest.fixture()
def fake_notifier(monkeypatch):
    FakeNotificationService.sent = []
    FakeNotificationService.enabled = True
    FakeNotificationService.should_succeed = True
    monkeypatch.setattr(scheduler_module, "NotificationService", FakeNotificationService)
    return FakeNotificationService


# ---------------------------------------------------------------------------
# API: creation + validation
# ---------------------------------------------------------------------------

def test_create_trailing_stop_percent(client, db_session, monkeypatch):
    asset = make_asset(db_session, symbol="AAPL")
    _patch_quote(monkeypatch, 200.0)

    response = client.post(
        "/api/alerts/",
        json={"assetId": asset.id, "rule": "trailing_stop", "trailPercent": 8.0},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["rule"] == "trailing_stop"
    assert body["trailPercent"] == 8.0
    assert body["trailAmount"] is None
    assert body["threshold"] is None
    assert body["highWaterMark"] == 200.0
    assert body["currentStopPrice"] == pytest.approx(184.0)
    assert body["intent"] == "sell"  # defaults to sell for trailing stops


def test_create_trailing_stop_amount(client, db_session, monkeypatch):
    asset = make_asset(db_session, symbol="AAPL")
    _patch_quote(monkeypatch, 200.0)

    response = client.post(
        "/api/alerts/",
        json={"assetId": asset.id, "rule": "trailing_stop", "trailAmount": 15.0},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["highWaterMark"] == 200.0
    assert body["currentStopPrice"] == pytest.approx(185.0)


@pytest.mark.parametrize(
    "payload",
    [
        {},  # neither trail field
        {"trailPercent": 8.0, "trailAmount": 15.0},  # both
        {"trailPercent": -5.0},  # nonpositive
        {"trailPercent": 100.0},  # >= 100%
        {"trailPercent": 8.0, "threshold": 100.0},  # threshold on TSL
    ],
)
def test_create_trailing_stop_invalid_payloads_422(client, db_session, payload):
    asset = make_asset(db_session, symbol="AAPL")
    response = client.post(
        "/api/alerts/",
        json={"assetId": asset.id, "rule": "trailing_stop", **payload},
    )
    assert response.status_code == 422


def test_create_price_rule_requires_threshold_422(client, db_session):
    asset = make_asset(db_session, symbol="AAPL")
    response = client.post(
        "/api/alerts/",
        json={"assetId": asset.id, "rule": "price_below"},
    )
    assert response.status_code == 422


def test_create_price_rule_rejects_trail_fields_422(client, db_session):
    asset = make_asset(db_session, symbol="AAPL")
    response = client.post(
        "/api/alerts/",
        json={"assetId": asset.id, "rule": "price_below", "threshold": 100.0, "trailPercent": 5.0},
    )
    assert response.status_code == 422


def test_create_trailing_stop_quote_failure_400s(client, db_session, monkeypatch):
    asset = make_asset(db_session, symbol="AAPL")

    async def failing_get_quote(self, symbol, asset_type=None):
        raise RuntimeError("provider outage")

    monkeypatch.setattr(MarketDataService, "get_quote", failing_get_quote)

    response = client.post(
        "/api/alerts/",
        json={"assetId": asset.id, "rule": "trailing_stop", "trailPercent": 8.0},
    )
    assert response.status_code == 400


def test_create_price_alert_with_buy_intent(client, db_session):
    asset = make_asset(db_session, symbol="NVDA")
    response = client.post(
        "/api/alerts/",
        json={"assetId": asset.id, "rule": "price_below", "threshold": 150.0, "intent": "buy"},
    )
    assert response.status_code == 201
    assert response.json()["intent"] == "buy"


def test_create_price_alert_without_intent_stays_none(client, db_session):
    asset = make_asset(db_session, symbol="NVDA")
    response = client.post(
        "/api/alerts/",
        json={"assetId": asset.id, "rule": "price_above", "threshold": 500.0},
    )
    assert response.status_code == 201
    assert response.json()["intent"] is None


def test_reactivate_trailing_stop_reseeds_hwm_and_clears_notified(
    client, db_session, monkeypatch
):
    asset = make_asset(db_session, symbol="AAPL")
    alert = make_tsl(
        db_session, asset, trail_percent=8.0, hwm=300.0, is_active=False,
        triggered_at=NOW, triggered_price=276.0, notified_at=NOW,
    )
    _patch_quote(monkeypatch, 250.0)

    response = client.put(f"/api/alerts/{alert.id}", json={"isActive": True})
    assert response.status_code == 200
    body = response.json()
    assert body["isActive"] is True
    assert body["highWaterMark"] == 250.0  # restarted from current price
    assert body["triggeredAt"] is None

    db_session.refresh(alert)
    assert alert.notified_at is None


# ---------------------------------------------------------------------------
# Evaluation job: ratchet + trigger
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evaluate_ratchets_hwm_up(eval_db, monkeypatch, fake_notifier):
    asset = make_asset(eval_db, symbol="AAPL")
    alert = make_tsl(eval_db, asset, trail_percent=10.0, hwm=100.0)
    alert_id = alert.id
    _patch_quotes(monkeypatch, {"AAPL": {"price": 120.0}})

    await scheduler_module.evaluate_price_alerts()

    alert = reload(eval_db, alert_id)
    assert alert.high_water_mark == 120.0
    assert alert.is_active is True  # 120 > stop 108


@pytest.mark.asyncio
async def test_evaluate_never_ratchets_hwm_down(eval_db, monkeypatch, fake_notifier):
    asset = make_asset(eval_db, symbol="AAPL")
    alert = make_tsl(eval_db, asset, trail_percent=50.0, hwm=100.0)
    alert_id = alert.id
    _patch_quotes(monkeypatch, {"AAPL": {"price": 80.0}})

    await scheduler_module.evaluate_price_alerts()

    alert = reload(eval_db, alert_id)
    assert alert.high_water_mark == 100.0
    assert alert.is_active is True  # stop is 50, price 80 above it


@pytest.mark.asyncio
async def test_evaluate_triggers_percent_trail_boundary_inclusive(
    eval_db, monkeypatch, fake_notifier
):
    asset = make_asset(eval_db, symbol="AAPL")
    alert = make_tsl(eval_db, asset, trail_percent=8.0, hwm=100.0)
    alert_id = alert.id
    _patch_quotes(monkeypatch, {"AAPL": {"price": 92.0}})  # exactly the stop

    await scheduler_module.evaluate_price_alerts()

    alert = reload(eval_db, alert_id)
    assert alert.is_active is False
    assert alert.triggered_price == 92.0
    assert alert.triggered_at is not None


@pytest.mark.asyncio
async def test_evaluate_triggers_amount_trail(eval_db, monkeypatch, fake_notifier):
    asset = make_asset(eval_db, symbol="AAPL")
    alert = make_tsl(eval_db, asset, trail_amount=15.0, hwm=200.0)
    alert_id = alert.id
    _patch_quotes(monkeypatch, {"AAPL": {"price": 184.5}})  # below stop 185

    await scheduler_module.evaluate_price_alerts()

    alert = reload(eval_db, alert_id)
    assert alert.is_active is False
    assert alert.triggered_price == 184.5


@pytest.mark.asyncio
async def test_evaluate_seeds_missing_hwm_without_triggering(
    eval_db, monkeypatch, fake_notifier
):
    asset = make_asset(eval_db, symbol="AAPL")
    alert = make_tsl(eval_db, asset, trail_percent=8.0, hwm=None)
    alert_id = alert.id
    _patch_quotes(monkeypatch, {"AAPL": {"price": 150.0}})

    await scheduler_module.evaluate_price_alerts()

    alert = reload(eval_db, alert_id)
    assert alert.high_water_mark == 150.0
    assert alert.is_active is True


# ---------------------------------------------------------------------------
# Notification pass
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_triggered_alert_sends_notification_once(eval_db, monkeypatch, fake_notifier):
    asset = make_asset(eval_db, symbol="AAPL")
    alert = make_tsl(eval_db, asset, trail_percent=8.0, hwm=100.0)
    alert_id = alert.id
    _patch_quotes(monkeypatch, {"AAPL": {"price": 90.0}})

    await scheduler_module.evaluate_price_alerts()

    assert len(fake_notifier.sent) == 1
    assert "AAPL" in fake_notifier.sent[0]
    assert "SELL" in fake_notifier.sent[0]
    assert reload(eval_db, alert_id).notified_at is not None

    # Second cycle: already notified, nothing new sent.
    await scheduler_module.evaluate_price_alerts()
    assert len(fake_notifier.sent) == 1


@pytest.mark.asyncio
async def test_failed_send_is_retried_next_cycle(eval_db, monkeypatch, fake_notifier):
    asset = make_asset(eval_db, symbol="AAPL")
    alert = make_tsl(eval_db, asset, trail_percent=8.0, hwm=100.0)
    alert_id = alert.id
    _patch_quotes(monkeypatch, {"AAPL": {"price": 90.0}})

    fake_notifier.should_succeed = False
    await scheduler_module.evaluate_price_alerts()
    assert reload(eval_db, alert_id).notified_at is None  # not marked, will retry

    fake_notifier.should_succeed = True
    await scheduler_module.evaluate_price_alerts()
    assert reload(eval_db, alert_id).notified_at is not None
    assert len(fake_notifier.sent) == 2  # one failed attempt + one success


@pytest.mark.asyncio
async def test_disabled_channel_marks_handled_without_sending(
    eval_db, monkeypatch, fake_notifier
):
    asset = make_asset(eval_db, symbol="AAPL")
    alert = make_tsl(eval_db, asset, trail_percent=8.0, hwm=100.0)
    alert_id = alert.id
    _patch_quotes(monkeypatch, {"AAPL": {"price": 90.0}})

    fake_notifier.enabled = False
    await scheduler_module.evaluate_price_alerts()

    assert fake_notifier.sent == []
    # Marked handled so a backlog doesn't blast out if a bot is added later.
    assert reload(eval_db, alert_id).notified_at is not None


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

def test_format_trailing_stop_message(db_session):
    asset = make_asset(db_session, symbol="AAPL")
    alert = make_tsl(
        db_session, asset, trail_percent=8.0, hwm=327.50,
        is_active=False, triggered_at=NOW, triggered_price=301.10,
    )
    message = format_alert_message(alert)
    assert message.startswith("🔴 SELL")
    assert "trailing stop hit: AAPL at $301.10" in message
    assert "high $327.50" in message
    assert "trail 8%" in message


def test_format_buy_target_message(db_session):
    asset = make_asset(db_session, symbol="NVDA")
    alert = PriceAlert(
        id=str(uuid.uuid4()),
        asset_id=asset.id,
        rule=AlertRule.PRICE_BELOW,
        threshold=150.0,
        intent=AlertIntent.BUY,
        is_active=False,
        triggered_at=NOW,
        triggered_price=149.20,
    )
    db_session.add(alert)
    db_session.commit()

    message = format_alert_message(alert)
    assert message.startswith("🟢 BUY")
    assert "NVDA ≤ $150.00" in message
    assert "now $149.20" in message


def test_format_message_without_intent_is_neutral(db_session):
    asset = make_asset(db_session, symbol="MSFT")
    alert = PriceAlert(
        id=str(uuid.uuid4()),
        asset_id=asset.id,
        rule=AlertRule.PRICE_ABOVE,
        threshold=500.0,
        is_active=False,
        triggered_at=NOW,
        triggered_price=501.0,
    )
    db_session.add(alert)
    db_session.commit()

    assert format_alert_message(alert).startswith("🔔 ALERT")


# ---------------------------------------------------------------------------
# Real NotificationService (disabled path only — no network)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_notification_service_disabled_without_config():
    service = notifications_module.NotificationService(bot_token="", chat_id="")
    assert service.enabled is False
    assert await service.send("hello") is False
