"""Alerts never evaluate or ratchet on stale/fallback prices; quote_refresh;
the price-job failure rule (spec §3 and §5)."""
import logging
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.asset import AssetType, Currency
from app.models.holding import HoldingLot
from app.services.market_data import MarketDataService
from app.services.sessions import QuoteBatchError, check_quote_batch
from app.tasks import scheduler as scheduler_module
from app.tasks.scheduler import build_scheduler
from tests.factories import make_alert, make_asset, make_tsl, patch_quotes, reload_alert

SUN_0900Z = datetime(2026, 11, 22, 9, 0, tzinfo=timezone.utc)  # US+TASE closed, crypto open


@pytest.mark.asyncio
async def test_stale_quote_no_trigger_and_hwm_unchanged(eval_db, monkeypatch):
    asset = make_asset(eval_db, symbol="TSL")
    aid = make_tsl(eval_db, asset, trail_percent=10, hwm=100.0).id
    patch_quotes(monkeypatch, {"TSL": {"price": 50.0, "stale": True}})
    with pytest.raises(QuoteBatchError):
        await scheduler_module.evaluate_price_alerts()
    a = reload_alert(eval_db, aid)
    assert a.is_active and a.triggered_at is None and a.high_water_mark == 100.0


@pytest.mark.asyncio
async def test_polygon_quote_not_alertable(eval_db, monkeypatch):
    asset = make_asset(eval_db, symbol="PLG")
    aid = make_alert(eval_db, asset, threshold=100.0).id
    patch_quotes(monkeypatch, {"PLG": {"price": 50.0, "source": "polygon"}})
    with pytest.raises(QuoteBatchError):
        await scheduler_module.evaluate_price_alerts()
    assert reload_alert(eval_db, aid).is_active


@pytest.mark.asyncio
async def test_cached_quote_without_market_time_is_skipped(eval_db, monkeypatch):
    asset = make_asset(eval_db, symbol="NMT")
    aid = make_tsl(eval_db, asset, trail_percent=10, hwm=100.0).id
    patch_quotes(monkeypatch, {"NMT": {"price": 50.0, "source": "yahoo", "marketTime": None}})
    with pytest.raises(QuoteBatchError):
        await scheduler_module.evaluate_price_alerts()
    a = reload_alert(eval_db, aid)
    assert a.is_active and a.high_water_mark == 100.0


@pytest.mark.asyncio
async def test_non_alertable_logs_one_warning_with_counts(eval_db, monkeypatch, caplog):
    for sym in ("S1", "S2"):
        make_alert(eval_db, make_asset(eval_db, symbol=sym), threshold=100.0)
    patch_quotes(monkeypatch, {s: {"price": 50.0, "stale": True} for s in ("S1", "S2")})
    with caplog.at_level(logging.WARNING), pytest.raises(QuoteBatchError):
        await scheduler_module.evaluate_price_alerts()
    msgs = [r.getMessage() for r in caplog.records if "non-alertable" in r.getMessage()]
    assert len(msgs) == 1 and "stale=2" in msgs[0]


@pytest.mark.asyncio
async def test_fund_alert_skipped_not_monitored(eval_db, monkeypatch):
    asset = make_asset(eval_db, symbol="FND")
    asset.asset_type = AssetType.FUND
    eval_db.commit()
    aid = make_alert(eval_db, asset, threshold=100.0).id
    patch_quotes(monkeypatch, {"FND": {"price": 50.0}})
    await scheduler_module.evaluate_price_alerts()  # not_monitored is neutral: no failure
    assert reload_alert(eval_db, aid).is_active


def _three(eval_db, monkeypatch, stale_symbols):
    ids = {}
    for sym in ("A1", "A2", "A3"):
        ids[sym] = make_alert(eval_db, make_asset(eval_db, symbol=sym), threshold=100.0).id
    patch_quotes(monkeypatch, {
        s: {"price": 50.0, **({"stale": True} if s in stale_symbols else {})} for s in ids
    })
    return ids


@pytest.mark.asyncio
async def test_evaluate_fails_when_majority_non_alertable(eval_db, monkeypatch):
    ids = _three(eval_db, monkeypatch, {"A1", "A2"})
    with pytest.raises(QuoteBatchError):
        await scheduler_module.evaluate_price_alerts()
    # the healthy symbol still triggered before the job raised
    assert reload_alert(eval_db, ids["A3"]).triggered_at is not None


@pytest.mark.asyncio
async def test_evaluate_ok_when_minority_non_alertable(eval_db, monkeypatch):
    ids = _three(eval_db, monkeypatch, {"A1"})
    await scheduler_module.evaluate_price_alerts()
    assert reload_alert(eval_db, ids["A2"]).triggered_at is not None


def test_old_market_time_and_not_monitored_are_neutral():
    check_quote_batch({"A": "old_market_time", "B": "old_market_time", "F": "not_monitored"})
    check_quote_batch({"A": "ok", "B": "old_market_time", "C": "old_market_time"})
    with pytest.raises(QuoteBatchError):
        check_quote_batch({"A": "ok", "B": "stale", "C": "old_market_time", "D": "error"})
    check_quote_batch({})


def test_batch_error_message_has_counts_only():
    with pytest.raises(QuoteBatchError) as ei:
        check_quote_batch({"SECRETSYM": "stale", "B": "source", "C": "ok"})
    msg = str(ei.value)
    assert "SECRETSYM" not in msg and "source=1" in msg and "stale=1" in msg


def _crypto_and_stock(db, lots=False):
    btc = make_asset(db, symbol="BTC", currency=Currency.USD)
    btc.asset_type = AssetType.CRYPTO
    db.commit()
    make_alert(db, btc, threshold=1.0)
    aapl = make_asset(db, symbol="AAPL")
    if lots:
        db.add(HoldingLot(id=str(uuid.uuid4()), asset_id=aapl.id, quantity=1, avg_cost=1.0,
                          cost_currency=Currency.USD, account_name="a",
                          purchase_date=SUN_0900Z - timedelta(days=5)))
    else:
        make_alert(db, aapl, threshold=1.0)
    db.commit()


def _fetch_recorder(monkeypatch, results):
    fetched = []

    async def get_quotes(self, symbols):
        fetched.extend(symbols)
        return {s: results(s) for s in symbols}

    monkeypatch.setattr(MarketDataService, "get_quotes", get_quotes)
    return fetched


def _ok(symbol):
    return {"price": 1.0, "source": "coingecko", "marketTime": SUN_0900Z.isoformat()}


@pytest.mark.asyncio
@pytest.mark.parametrize("lots", [False, True])
async def test_quote_refresh_fetches_only_in_session_watched(eval_db, monkeypatch, lots):
    monkeypatch.setattr(scheduler_module, "_utcnow", lambda: SUN_0900Z)
    _crypto_and_stock(eval_db, lots=lots)
    fetched = _fetch_recorder(monkeypatch, _ok)
    await scheduler_module.quote_refresh()
    assert fetched == ["BTC"]


@pytest.mark.asyncio
async def test_quote_refresh_raises_when_zero_succeed(eval_db, monkeypatch):
    monkeypatch.setattr(scheduler_module, "_utcnow", lambda: SUN_0900Z)
    _crypto_and_stock(eval_db)
    _fetch_recorder(monkeypatch, lambda s: {"error": "boom"})
    with pytest.raises(QuoteBatchError):
        await scheduler_module.quote_refresh()


@pytest.mark.asyncio
async def test_quote_refresh_no_watched_assets_is_success(eval_db, monkeypatch):
    monkeypatch.setattr(scheduler_module, "_utcnow", lambda: SUN_0900Z)
    fetched = _fetch_recorder(monkeypatch, _ok)
    await scheduler_module.quote_refresh()
    assert fetched == []


@pytest.mark.asyncio
async def test_quote_refresh_job_registered():
    sched = build_scheduler()
    sched.start(paused=True)
    try:
        refresh = sched.get_job("quote_refresh")
        evaluate = sched.get_job("price_alert_evaluation")
    finally:
        sched.shutdown(wait=False)
    assert refresh.trigger.interval == timedelta(minutes=5)
    assert refresh.misfire_grace_time == 120
    assert refresh.func.__heartbeat_name__ == "quote_refresh"
    gap = evaluate.next_run_time - refresh.next_run_time
    assert evaluate.trigger.interval == timedelta(minutes=5)
    assert timedelta(seconds=-290) < gap < timedelta(seconds=-250)  # first eval at +30 s
