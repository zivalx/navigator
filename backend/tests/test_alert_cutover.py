"""Pre-cutover alerts are paused until the audit converts them (Ruling 2)."""
import logging
from datetime import datetime, timedelta, timezone

import pytest

from app.models.alert import AlertRule
from app.models.asset import Currency, MarketRegion
from app.models.maintenance import ALERTS_CONVERTED_MARKER, CUTOVER_MARKER, set_marker
from app.tasks import scheduler as scheduler_module
from tests.factories import make_alert, make_asset, make_tsl, patch_quotes, reload_alert

T = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)


def _setup(db, created_at, threshold=2600.0, rule=AlertRule.PRICE_BELOW):
    asset = make_asset(db, symbol="TEVA.TA", currency=Currency.ILS, exchange="TLV",
                       market_region=MarketRegion.IL)
    alert = make_alert(db, asset, rule=rule, threshold=threshold)
    alert.created_at = created_at
    db.commit()
    return alert.id


@pytest.mark.asyncio
async def test_pre_cutover_alert_paused_on_minor_quote(eval_db, monkeypatch, caplog):
    set_marker(eval_db, CUTOVER_MARKER, at=T)
    aid = _setup(eval_db, T - timedelta(hours=1))
    patch_quotes(monkeypatch, {"TEVA.TA": {"price": 26.0, "unitFactor": 0.01}})
    with caplog.at_level(logging.WARNING):
        await scheduler_module.evaluate_price_alerts()
    a = reload_alert(eval_db, aid)
    assert a.is_active and a.triggered_at is None
    assert any("paused pre-cutover alerts" in r.getMessage() and "TEVA.TA" in r.getMessage()
               for r in caplog.records)


@pytest.mark.asyncio
async def test_pre_cutover_alert_paused_on_stale_quote(eval_db, monkeypatch, caplog):
    set_marker(eval_db, CUTOVER_MARKER, at=T)
    aid = _setup(eval_db, T - timedelta(hours=1))
    patch_quotes(monkeypatch, {"TEVA.TA": {"price": 26.0, "stale": True}})
    with caplog.at_level(logging.WARNING):
        await scheduler_module.evaluate_price_alerts()
    a = reload_alert(eval_db, aid)
    assert a.is_active and a.triggered_at is None
    assert any("TEVA.TA" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_pre_cutover_alert_runs_after_conversion_marker(eval_db, monkeypatch):
    set_marker(eval_db, CUTOVER_MARKER, at=T)
    set_marker(eval_db, ALERTS_CONVERTED_MARKER)
    aid = _setup(eval_db, T - timedelta(hours=1), threshold=26.5)
    patch_quotes(monkeypatch, {"TEVA.TA": {"price": 26.0, "unitFactor": 0.01}})
    await scheduler_module.evaluate_price_alerts()
    assert not reload_alert(eval_db, aid).is_active


@pytest.mark.asyncio
async def test_post_cutover_alert_evaluates_normally(eval_db, monkeypatch):
    set_marker(eval_db, CUTOVER_MARKER, at=T)
    aid = _setup(eval_db, T + timedelta(hours=1), threshold=26.5)
    patch_quotes(monkeypatch, {"TEVA.TA": {"price": 26.0, "unitFactor": 0.01}})
    await scheduler_module.evaluate_price_alerts()
    assert not reload_alert(eval_db, aid).is_active


@pytest.mark.asyncio
async def test_no_cutover_marker_means_no_pause(eval_db, monkeypatch):
    aid = _setup(eval_db, T - timedelta(hours=1), threshold=26.5)
    patch_quotes(monkeypatch, {"TEVA.TA": {"price": 26.0, "unitFactor": 0.01}})
    await scheduler_module.evaluate_price_alerts()
    assert not reload_alert(eval_db, aid).is_active


@pytest.mark.asyncio
async def test_paused_trailing_stop_does_not_ratchet(eval_db, monkeypatch):
    set_marker(eval_db, CUTOVER_MARKER, at=T)
    asset = make_asset(eval_db, symbol="TEVA.TA", currency=Currency.ILS, exchange="TLV",
                       market_region=MarketRegion.IL)
    alert = make_tsl(eval_db, asset, trail_percent=10, hwm=2600.0)
    alert.created_at = T - timedelta(hours=1)
    eval_db.commit()
    aid = alert.id
    patch_quotes(monkeypatch, {"TEVA.TA": {"price": 30.0, "unitFactor": 0.01, "stale": False}})
    await scheduler_module.evaluate_price_alerts()
    a = reload_alert(eval_db, aid)
    assert a.is_active and a.high_water_mark == 2600.0


@pytest.mark.asyncio
async def test_null_created_at_alert_paused_like_pre_cutover(eval_db, monkeypatch):
    set_marker(eval_db, CUTOVER_MARKER, at=T)
    aid = _setup(eval_db, None)
    patch_quotes(monkeypatch, {"TEVA.TA": {"price": 26.0, "unitFactor": 0.01}})
    await scheduler_module.evaluate_price_alerts()
    a = reload_alert(eval_db, aid)
    assert a.is_active and a.triggered_at is None
