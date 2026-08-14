"""Tests for trailing-stop alerts, buy/sell intents, and Telegram notification.

Covers docs/superpowers/specs/2026-08-12-trailing-stop-alerts-design.md:
- create/update validation (shared rule-shape rules, intent defaulting)
- high-water-mark seeding on create, re-seeding on reactivate/rule-switch
- the evaluation job: ratchet up (never down), %- and $-trail triggering with
  inclusive boundary, legacy alerts without a mark
- the notification pass: send-once, retry-after-failure, skipped-channel
  bookkeeping (notification_skipped_at, distinct from delivered), formatting.

db_session/client/eval_db come from conftest.py; model factories from
tests.factories.
"""
from datetime import datetime, timezone

import pytest

from app.models.alert import AlertIntent, AlertRule, PriceAlert
from app.services import notifications as notifications_module
from app.services.market_data import MarketDataService
from app.services.notifications import format_alert_message
from app.tasks import scheduler as scheduler_module
from tests.factories import (
    make_alert,
    make_asset,
    make_tsl,
    patch_quote,
    patch_quotes,
    reload_alert,
)

NOW = datetime.now(timezone.utc)


class FakeNotificationService:
    """Instance-based stand-in; install with `install(monkeypatch)`."""

    def __init__(self, enabled=True, should_succeed=True):
        self.enabled = enabled
        self.should_succeed = should_succeed
        self.sent = []

    async def send(self, text: str) -> bool:
        if not self.enabled:
            return False
        self.sent.append(text)
        return self.should_succeed

    def install(self, monkeypatch):
        monkeypatch.setattr(scheduler_module, "NotificationService", lambda: self)
        return self


@pytest.fixture()
def fake_notifier(monkeypatch):
    return FakeNotificationService().install(monkeypatch)


# ---------------------------------------------------------------------------
# API: creation + validation
# ---------------------------------------------------------------------------

def test_create_trailing_stop_percent(client, db_session, monkeypatch):
    asset = make_asset(db_session, symbol="AAPL")
    patch_quote(monkeypatch, 200.0)

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
    patch_quote(monkeypatch, 200.0)

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


# ---------------------------------------------------------------------------
# API: update validation parity + rule switching
# ---------------------------------------------------------------------------

def test_update_rejects_trail_percent_at_or_above_100(client, db_session):
    asset = make_asset(db_session, symbol="AAPL")
    alert = make_tsl(db_session, asset, trail_percent=8.0, hwm=300.0)

    response = client.put(f"/api/alerts/{alert.id}", json={"trailPercent": 150.0})
    assert response.status_code == 400


def test_update_rejects_explicit_threshold_on_trailing_stop(client, db_session):
    asset = make_asset(db_session, symbol="AAPL")
    alert = make_tsl(db_session, asset, trail_percent=8.0, hwm=300.0)

    response = client.put(f"/api/alerts/{alert.id}", json={"threshold": 120.0})
    assert response.status_code == 400


def test_update_rejects_both_trail_fields(client, db_session):
    asset = make_asset(db_session, symbol="AAPL")
    alert = make_tsl(db_session, asset, trail_percent=8.0, hwm=300.0)

    response = client.put(
        f"/api/alerts/{alert.id}", json={"trailPercent": 8.0, "trailAmount": 15.0}
    )
    assert response.status_code == 400


def test_update_switch_tsl_to_price_rule_clears_trail_state(client, db_session):
    asset = make_asset(db_session, symbol="AAPL")
    alert = make_tsl(db_session, asset, trail_percent=8.0, hwm=300.0)

    response = client.put(
        f"/api/alerts/{alert.id}", json={"rule": "price_below", "threshold": 250.0}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["rule"] == "price_below"
    assert body["threshold"] == 250.0
    assert body["trailPercent"] is None
    assert body["trailAmount"] is None
    assert body["highWaterMark"] is None


def test_update_switch_price_rule_to_tsl_clears_threshold_and_seeds(
    client, db_session, monkeypatch
):

    asset = make_asset(db_session, symbol="AAPL")
    alert = make_alert(db_session, asset, rule=AlertRule.PRICE_BELOW, threshold=250.0)
    patch_quote(monkeypatch, 310.0)

    response = client.put(
        f"/api/alerts/{alert.id}", json={"rule": "trailing_stop", "trailPercent": 8.0}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["rule"] == "trailing_stop"
    assert body["threshold"] is None  # stale threshold cleared, not carried over
    assert body["highWaterMark"] == 310.0
    assert body["intent"] == "sell"  # defaulted on switch


def test_update_can_clear_intent_with_explicit_null(client, db_session):

    asset = make_asset(db_session, symbol="NVDA")
    alert = make_alert(db_session, asset, rule=AlertRule.PRICE_BELOW, threshold=150.0)
    client.put(f"/api/alerts/{alert.id}", json={"intent": "buy"})

    response = client.put(f"/api/alerts/{alert.id}", json={"intent": None})
    assert response.status_code == 200
    assert response.json()["intent"] is None


def test_reactivate_trailing_stop_reseeds_hwm_and_clears_notification_state(
    client, db_session, monkeypatch
):
    asset = make_asset(db_session, symbol="AAPL")
    alert = make_tsl(
        db_session, asset, trail_percent=8.0, hwm=300.0, is_active=False,
        triggered_at=NOW, triggered_price=276.0, notified_at=NOW,
        notification_skipped_at=NOW,
    )
    patch_quote(monkeypatch, 250.0)

    response = client.put(f"/api/alerts/{alert.id}", json={"isActive": True})
    assert response.status_code == 200
    body = response.json()
    assert body["isActive"] is True
    assert body["highWaterMark"] == 250.0  # restarted from current price
    assert body["triggeredAt"] is None

    db_session.refresh(alert)
    assert alert.notified_at is None
    assert alert.notification_skipped_at is None


# ---------------------------------------------------------------------------
# Evaluation job: ratchet + trigger
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evaluate_ratchets_hwm_up(eval_db, monkeypatch, fake_notifier):
    asset = make_asset(eval_db, symbol="AAPL")
    alert = make_tsl(eval_db, asset, trail_percent=10.0, hwm=100.0)
    alert_id = alert.id
    patch_quotes(monkeypatch, {"AAPL": {"price": 120.0}})

    await scheduler_module.evaluate_price_alerts()

    alert = reload_alert(eval_db, alert_id)
    assert alert.high_water_mark == 120.0
    assert alert.is_active is True  # 120 > stop 108


@pytest.mark.asyncio
async def test_evaluate_never_ratchets_hwm_down(eval_db, monkeypatch, fake_notifier):
    asset = make_asset(eval_db, symbol="AAPL")
    alert = make_tsl(eval_db, asset, trail_percent=50.0, hwm=100.0)
    alert_id = alert.id
    patch_quotes(monkeypatch, {"AAPL": {"price": 80.0}})

    await scheduler_module.evaluate_price_alerts()

    alert = reload_alert(eval_db, alert_id)
    assert alert.high_water_mark == 100.0
    assert alert.is_active is True  # stop is 50, price 80 above it


@pytest.mark.asyncio
async def test_evaluate_triggers_percent_trail_boundary_inclusive(
    eval_db, monkeypatch, fake_notifier
):
    asset = make_asset(eval_db, symbol="AAPL")
    alert = make_tsl(eval_db, asset, trail_percent=8.0, hwm=100.0)
    alert_id = alert.id
    patch_quotes(monkeypatch, {"AAPL": {"price": 92.0}})  # exactly the stop

    await scheduler_module.evaluate_price_alerts()

    alert = reload_alert(eval_db, alert_id)
    assert alert.is_active is False
    assert alert.triggered_price == 92.0
    assert alert.triggered_at is not None


@pytest.mark.asyncio
async def test_evaluate_triggers_amount_trail(eval_db, monkeypatch, fake_notifier):
    asset = make_asset(eval_db, symbol="AAPL")
    alert = make_tsl(eval_db, asset, trail_amount=15.0, hwm=200.0)
    alert_id = alert.id
    patch_quotes(monkeypatch, {"AAPL": {"price": 184.5}})  # below stop 185

    await scheduler_module.evaluate_price_alerts()

    alert = reload_alert(eval_db, alert_id)
    assert alert.is_active is False
    assert alert.triggered_price == 184.5


@pytest.mark.asyncio
async def test_evaluate_seeds_missing_hwm_without_triggering(
    eval_db, monkeypatch, fake_notifier
):
    asset = make_asset(eval_db, symbol="AAPL")
    alert = make_tsl(eval_db, asset, trail_percent=8.0, hwm=None)
    alert_id = alert.id
    patch_quotes(monkeypatch, {"AAPL": {"price": 150.0}})

    await scheduler_module.evaluate_price_alerts()

    alert = reload_alert(eval_db, alert_id)
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
    patch_quotes(monkeypatch, {"AAPL": {"price": 90.0}})

    await scheduler_module.evaluate_price_alerts()

    assert len(fake_notifier.sent) == 1
    assert "AAPL" in fake_notifier.sent[0]
    assert "SELL" in fake_notifier.sent[0]
    assert reload_alert(eval_db, alert_id).notified_at is not None

    # Second cycle: already notified, nothing new sent.
    await scheduler_module.evaluate_price_alerts()
    assert len(fake_notifier.sent) == 1


@pytest.mark.asyncio
async def test_failed_send_is_retried_next_cycle(eval_db, monkeypatch, fake_notifier):
    asset = make_asset(eval_db, symbol="AAPL")
    alert = make_tsl(eval_db, asset, trail_percent=8.0, hwm=100.0)
    alert_id = alert.id
    patch_quotes(monkeypatch, {"AAPL": {"price": 90.0}})

    fake_notifier.should_succeed = False
    await scheduler_module.evaluate_price_alerts()
    assert reload_alert(eval_db, alert_id).notified_at is None  # not marked, will retry

    fake_notifier.should_succeed = True
    await scheduler_module.evaluate_price_alerts()
    assert reload_alert(eval_db, alert_id).notified_at is not None
    assert len(fake_notifier.sent) == 2  # one failed attempt + one success


@pytest.mark.asyncio
async def test_disabled_channel_marks_skipped_not_delivered(
    eval_db, monkeypatch, fake_notifier
):
    asset = make_asset(eval_db, symbol="AAPL")
    alert = make_tsl(eval_db, asset, trail_percent=8.0, hwm=100.0)
    alert_id = alert.id
    patch_quotes(monkeypatch, {"AAPL": {"price": 90.0}})

    fake_notifier.enabled = False
    await scheduler_module.evaluate_price_alerts()

    assert fake_notifier.sent == []
    alert = reload_alert(eval_db, alert_id)
    # Skipped is bookkept separately from delivered — a config gap stays
    # auditable, while the backlog still won't blast out if a bot is added.
    assert alert.notified_at is None
    assert alert.notification_skipped_at is not None

    # And a skipped alert is not retried on later cycles.
    fake_notifier.enabled = True
    await scheduler_module.evaluate_price_alerts()
    assert fake_notifier.sent == []


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
    import uuid

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
    import uuid

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
