"""eod_snapshot: one EOD row per (asset, trade_date), refreshed in place."""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

import logging

from app.models.asset import AssetType, Currency, MarketRegion
from app.models.holding import HoldingLot
from app.models.price import PriceSnapshot
from app.services.market_data import MarketDataService
from app.tasks import scheduler as scheduler_module
from tests.factories import make_asset

FRI_CLOSE = datetime(2026, 11, 20, 21, 0, tzinfo=timezone.utc)
SAT_NIGHT = datetime(2026, 11, 21, 21, 30, tzinfo=timezone.utc)


def _hold(db, asset):
    db.add(HoldingLot(id=str(uuid.uuid4()), asset_id=asset.id, quantity=1, avg_cost=1.0,
                      cost_currency=Currency.USD, account_name="a",
                      purchase_date=datetime.now(timezone.utc) - timedelta(days=5)))
    db.commit()


def _setup(monkeypatch, now, quote):
    """Pin the job clock and the quote; `quote` is a dict the test may mutate."""
    async def get_quote(self, symbol, asset_type=None):
        return dict(quote)

    async def no_portfolio(self, day):
        return None

    monkeypatch.setattr(scheduler_module, "_utcnow", lambda: now)
    monkeypatch.setattr(MarketDataService, "get_quote", get_quote)
    monkeypatch.setattr(scheduler_module.PortfolioService, "compute_daily_snapshot", no_portfolio)


def _eod_rows(db):
    db.expire_all()
    return db.query(PriceSnapshot).filter(PriceSnapshot.trade_date.isnot(None)).all()


@pytest.mark.asyncio
async def test_eod_twice_same_day_one_row_refreshed_price(eval_db, monkeypatch):
    asset = make_asset(eval_db, symbol="AAPL")
    _hold(eval_db, asset)
    quote = {"price": 100.0, "currency": "USD", "source": "yahoo",
             "marketTime": FRI_CLOSE.isoformat()}
    _setup(monkeypatch, FRI_CLOSE + timedelta(minutes=30), quote)

    await scheduler_module.snapshot_eod_prices()
    quote["price"] = 101.0
    await scheduler_module.snapshot_eod_prices()

    rows = _eod_rows(eval_db)
    assert len(rows) == 1
    assert rows[0].price == 101.0
    assert rows[0].source == "eod_snapshot"


@pytest.mark.asyncio
async def test_eod_saturday_run_writes_friday_trade_date(eval_db, monkeypatch):
    asset = make_asset(eval_db, symbol="AAPL")
    _hold(eval_db, asset)
    quote = {"price": 100.0, "currency": "USD", "source": "yahoo",
             "marketTime": FRI_CLOSE.isoformat()}
    _setup(monkeypatch, SAT_NIGHT, quote)

    await scheduler_module.snapshot_eod_prices()

    rows = _eod_rows(eval_db)
    assert [r.trade_date for r in rows] == [date(2026, 11, 20)]
    assert not [r for r in rows if r.trade_date == date(2026, 11, 21)]


@pytest.mark.asyncio
async def test_eod_tase_trade_date_from_market_time(eval_db, monkeypatch):
    asset = make_asset(eval_db, symbol="TEVA.TA", currency=Currency.ILS,
                       exchange="TASE", market_region=MarketRegion.IL)
    _hold(eval_db, asset)
    quote = {"price": 55.0, "currency": "ILS", "source": "yahoo",
             "exchangeTimezone": "Asia/Jerusalem",
             "marketTime": datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc).isoformat()}
    _setup(monkeypatch, SAT_NIGHT, quote)

    await scheduler_module.snapshot_eod_prices()

    rows = _eod_rows(eval_db)
    assert [r.trade_date for r in rows] == [date(2026, 11, 20)]


@pytest.mark.asyncio
async def test_eod_fund_with_yahoo_quote_gets_a_row(eval_db, monkeypatch):
    asset = make_asset(eval_db, symbol="VFIAX")
    asset.asset_type = AssetType.FUND
    eval_db.commit()
    _hold(eval_db, asset)
    quote = {"price": 400.0, "currency": "USD", "source": "yahoo",
             "marketTime": FRI_CLOSE.isoformat()}
    _setup(monkeypatch, SAT_NIGHT, quote)

    await scheduler_module.snapshot_eod_prices()

    rows = _eod_rows(eval_db)
    assert [(r.price, r.trade_date) for r in rows] == [(400.0, date(2026, 11, 20))]


@pytest.mark.asyncio
async def test_eod_polygon_fallback_gets_row_on_its_own_trade_date(eval_db, monkeypatch):
    asset = make_asset(eval_db, symbol="AAPL")
    _hold(eval_db, asset)
    thursday = datetime(2026, 11, 19, 21, 0, tzinfo=timezone.utc)
    quote = {"price": 99.0, "currency": "USD", "source": "polygon",
             "timestamp": thursday.isoformat()}  # previous-day bar, no marketTime
    _setup(monkeypatch, FRI_CLOSE + timedelta(minutes=30), quote)

    await scheduler_module.snapshot_eod_prices()

    rows = _eod_rows(eval_db)
    assert [(r.price, r.trade_date) for r in rows] == [(99.0, date(2026, 11, 19))]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra, reason",
    [
        ({"stale": True}, "stale"),
        ({"source": "db_cache"}, "db_cache"),
        ({"marketTime": None}, "no_timestamp"),
    ],
)
async def test_eod_skips_ineligible_quote_and_counts_it(eval_db, monkeypatch, caplog, extra, reason):
    asset = make_asset(eval_db, symbol="AAPL")
    _hold(eval_db, asset)
    quote = {"price": 100.0, "currency": "USD", "source": "yahoo",
             "marketTime": FRI_CLOSE.isoformat(), **extra}
    _setup(monkeypatch, FRI_CLOSE + timedelta(minutes=30), quote)

    with caplog.at_level(logging.INFO, logger=scheduler_module.logger.name):
        await scheduler_module.snapshot_eod_prices()  # must not raise

    assert _eod_rows(eval_db) == []
    complete = [r.getMessage() for r in caplog.records if "EOD snapshot complete" in r.getMessage()]
    assert complete and f"1 skipped {{'{reason}': 1}}" in complete[0]
    assert "AAPL" not in complete[0]


@pytest.mark.asyncio
async def test_eod_crypto_rerun_keeps_earlier_timestamp(eval_db, monkeypatch):
    asset = make_asset(eval_db, symbol="BTC", exchange="CRYPTO")
    asset.asset_type = AssetType.CRYPTO
    eval_db.commit()
    _hold(eval_db, asset)
    quote = {"price": 100.0, "currency": "USD", "source": "coingecko",
             "timestamp": FRI_CLOSE.isoformat()}
    first = FRI_CLOSE + timedelta(minutes=30)
    _setup(monkeypatch, first, quote)
    await scheduler_module.snapshot_eod_prices()

    quote["price"] = 101.0
    monkeypatch.setattr(scheduler_module, "_utcnow", lambda: first + timedelta(hours=2))
    await scheduler_module.snapshot_eod_prices()

    rows = _eod_rows(eval_db)
    assert len(rows) == 1 and rows[0].price == 101.0
    ts = rows[0].timestamp if rows[0].timestamp.tzinfo else rows[0].timestamp.replace(tzinfo=timezone.utc)
    assert ts == first
