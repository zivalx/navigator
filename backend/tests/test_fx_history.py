import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import FxDailyRate
from app.models.asset import Currency
from app.providers.yahoo import YahooFinanceProvider


def _row(day, per_usd):
    return FxDailyRate(
        id=str(uuid.uuid4()),
        currency=Currency.ILS,
        date=day,
        per_usd=per_usd,
        fetched_at=datetime.now(timezone.utc),
    )


def test_fx_daily_rate_unique_per_currency_and_date(db_session):
    db_session.add(_row(date(2026, 11, 19), 3.27))
    db_session.commit()
    db_session.add(_row(date(2026, 11, 19), 3.30))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


class _Resp:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


@pytest.mark.asyncio
async def test_get_fx_history_parses_closes():
    body = {
        "chart": {
            "result": [
                {
                    "timestamp": [1795046400, 1795132800, 1795219200],
                    "indicators": {"quote": [{"close": [3.27, None, 3.33]}]},
                }
            ]
        }
    }
    calls = []

    async def fake_get(url, params=None, headers=None):
        calls.append((url, params))
        return _Resp(body)

    provider = YahooFinanceProvider()
    provider.client.get = fake_get

    result = await provider.get_fx_history("ILS", date(2026, 11, 19), date(2026, 11, 21))

    assert result == [(date(2026, 11, 19), 3.27), (date(2026, 11, 21), 3.33)]
    url, params = calls[0]
    assert url == YahooFinanceProvider.QUERY_URL.format(symbol="ILS=X")
    assert params == {"interval": "1d", "period1": 1795046400, "period2": 1795305600}


@pytest.mark.asyncio
async def test_get_fx_history_empty_raises():
    async def fake_get(url, params=None, headers=None):
        return _Resp({"chart": {"result": None}})

    provider = YahooFinanceProvider()
    provider.client.get = fake_get
    with pytest.raises(ValueError):
        await provider.get_fx_history("ILS", date(2026, 11, 19), date(2026, 11, 21))


# ---- FxService.get_rate_at / convert_at ----

import logging

from app.services.fx import FxService

MON = date(2026, 11, 23)
FRI = date(2026, 11, 20)


def _seed(db, ccy, day, per_usd):
    db.add(
        FxDailyRate(
            id=str(uuid.uuid4()),
            currency=ccy,
            date=day,
            per_usd=per_usd,
            fetched_at=datetime.now(timezone.utc),
        )
    )
    db.commit()


def _stub_live(monkeypatch, value=3.7):
    async def fake(self, f, t):
        return value

    monkeypatch.setattr(FxService, "get_rate", fake)


@pytest.mark.asyncio
async def test_rate_at_exact_date(db_session):
    _seed(db_session, Currency.ILS, MON, 3.5)
    svc = FxService(db_session)
    assert await svc.get_rate_at(Currency.USD, Currency.ILS, MON) == 3.5
    assert await svc.get_rate_at(Currency.ILS, Currency.USD, MON) == pytest.approx(1 / 3.5)
    assert await svc.get_rate_at("USD", "ILS", MON) == 3.5


@pytest.mark.asyncio
async def test_rate_at_weekend_uses_prior_day(db_session):
    _seed(db_session, Currency.ILS, FRI, 3.5)
    svc = FxService(db_session)
    assert await svc.get_rate_at(Currency.USD, Currency.ILS, date(2026, 11, 21)) == 3.5
    assert await svc.get_rate_at(Currency.USD, Currency.ILS, date(2026, 11, 22)) == 3.5


@pytest.mark.asyncio
async def test_rate_at_gap_over_7_days_is_missing(db_session):
    _seed(db_session, Currency.ILS, date(2026, 11, 15), 3.5)
    svc = FxService(db_session)
    with pytest.raises(ValueError):
        await svc.get_rate_at(Currency.USD, Currency.ILS, MON, strict=True)
    # exactly 7 days old is still fine
    assert await svc.get_rate_at(Currency.USD, Currency.ILS, date(2026, 11, 22), strict=True) == 3.5


@pytest.mark.asyncio
async def test_rate_at_missing_falls_back_to_live_rate_once(db_session, monkeypatch, caplog):
    _stub_live(monkeypatch, 3.7)
    svc = FxService(db_session)
    with caplog.at_level(logging.WARNING, logger="app.services.fx"):
        assert await svc.get_rate_at(Currency.USD, Currency.ILS, MON) == 3.7
        assert await svc.get_rate_at(Currency.USD, Currency.ILS, FRI) == 3.7
    warnings = [r for r in caplog.records if "no daily rate" in r.getMessage()]
    assert len(warnings) == 1


@pytest.mark.asyncio
async def test_rate_at_cross_through_usd(db_session):
    _seed(db_session, Currency.ILS, MON, 3.5)
    _seed(db_session, Currency.EUR, MON, 0.875)
    svc = FxService(db_session)
    assert await svc.get_rate_at(Currency.ILS, Currency.EUR, MON) == pytest.approx(0.25)


@pytest.mark.asyncio
async def test_rate_at_nonpositive_is_missing(db_session):
    _seed(db_session, Currency.ILS, MON, 0.0)
    svc = FxService(db_session)
    with pytest.raises(ValueError):
        await svc.get_rate_at(Currency.ILS, Currency.USD, MON, strict=True)


@pytest.mark.asyncio
async def test_rate_at_same_currency_is_one(db_session):
    svc = FxService(db_session)
    assert await svc.get_rate_at(Currency.ILS, Currency.ILS, MON, strict=True) == 1.0
    assert await svc.get_rate_at(Currency.USD, Currency.USD, MON, strict=True) == 1.0


@pytest.mark.asyncio
async def test_convert_at_multiplies(db_session):
    _seed(db_session, Currency.ILS, MON, 3.5)
    svc = FxService(db_session)
    assert await svc.convert_at(100.0, Currency.USD, Currency.ILS, MON) == pytest.approx(350.0)


@pytest.mark.asyncio
async def test_get_fx_history_skips_nonpositive_and_null_chart():
    body = {
        "chart": {
            "result": [
                {
                    "timestamp": [1795046400, 1795132800, 1795219200],
                    "indicators": {"quote": [{"close": [0.0, -1.0, 3.33]}]},
                }
            ]
        }
    }

    async def fake_get(url, params=None, headers=None):
        return _Resp(body)

    provider = YahooFinanceProvider()
    provider.client.get = fake_get
    result = await provider.get_fx_history("ILS", date(2026, 11, 19), date(2026, 11, 21))
    assert result == [(date(2026, 11, 21), 3.33)]

    async def null_chart(url, params=None, headers=None):
        return _Resp({"chart": None})

    provider.client.get = null_chart
    with pytest.raises(ValueError):
        await provider.get_fx_history("ILS", date(2026, 11, 19), date(2026, 11, 21))


# ---- refresh_fx_history ----

from datetime import timedelta

from app.models.asset import Asset, AssetType, MarketRegion
from app.models.holding import HoldingLot
from app.services.fx_history import refresh_fx_history

TODAY = date(2026, 11, 25)
NON_USD = [c for c in Currency if c != Currency.USD]


class _FakeProvider:
    def __init__(self, value=3.0, fail=()):
        self.calls = []
        self.value = value
        self.fail = set(fail)
        self.closed = False

    async def get_fx_history(self, currency, start, end):
        self.calls.append((currency, start, end))
        if currency in self.fail:
            raise ValueError(f"boom {currency}")
        return [(start, self.value), (end, self.value + 1)]

    async def close(self):
        self.closed = True


def _add_lot(db, purchased):
    asset = Asset(
        id=str(uuid.uuid4()), symbol="AAA", name="AAA Inc.", exchange="NASDAQ",
        currency=Currency.USD, asset_type=AssetType.STOCK, market_region=MarketRegion.US,
    )
    db.add(asset)
    db.add(HoldingLot(
        id=str(uuid.uuid4()), asset_id=asset.id, quantity=1, avg_cost=1,
        cost_currency=Currency.USD, account_name="a", purchase_date=purchased,
    ))
    db.commit()


@pytest.mark.asyncio
async def test_refresh_backfills_all_currencies_from_needed_start(db_session):
    _add_lot(db_session, datetime(2025, 1, 10, tzinfo=timezone.utc))
    provider = _FakeProvider()
    count = await refresh_fx_history(db_session, provider=provider, today=TODAY)
    assert sorted(c[0] for c in provider.calls) == sorted(c.value for c in NON_USD)
    assert all(c[1] == date(2025, 1, 3) and c[2] == TODAY for c in provider.calls)
    assert count == 2 * len(NON_USD)
    assert db_session.query(FxDailyRate).count() == 2 * len(NON_USD)
    assert provider.closed is False


@pytest.mark.asyncio
async def test_refresh_is_idempotent_and_overwrites_partial_close(db_session):
    await refresh_fx_history(db_session, provider=_FakeProvider(3.0), today=TODAY)
    await refresh_fx_history(db_session, provider=_FakeProvider(9.0), today=TODAY)
    rows = db_session.query(FxDailyRate).filter_by(currency=Currency.ILS).all()
    assert len(rows) == len({r.date for r in rows})
    by_date = {r.date: r.per_usd for r in rows}
    assert by_date[TODAY] == 10.0
    assert by_date[TODAY - timedelta(days=407)] == 3.0


@pytest.mark.asyncio
async def test_refresh_incremental_window(db_session):
    needed = TODAY - timedelta(days=400) - timedelta(days=7)
    _seed(db_session, Currency.ILS, needed, 3.0)
    _seed(db_session, Currency.ILS, date(2026, 11, 20), 3.2)
    provider = _FakeProvider()
    await refresh_fx_history(db_session, provider=provider, today=TODAY)
    ils = [c for c in provider.calls if c[0] == "ILS"]
    assert ils == [("ILS", date(2026, 11, 15), TODAY)]


@pytest.mark.asyncio
async def test_refresh_backfills_gap_before_coverage(db_session):
    _add_lot(db_session, datetime(2025, 1, 10, tzinfo=timezone.utc))
    _seed(db_session, Currency.ILS, date(2026, 6, 1), 3.2)
    provider = _FakeProvider()
    await refresh_fx_history(db_session, provider=provider, today=TODAY)
    ils = [c for c in provider.calls if c[0] == "ILS"]
    assert ils == [("ILS", date(2025, 1, 3), TODAY)]


@pytest.mark.asyncio
async def test_refresh_one_failing_currency_does_not_stop_others(db_session, caplog):
    provider = _FakeProvider(fail={"EUR"})
    with caplog.at_level(logging.WARNING, logger="app.services.fx_history"):
        await refresh_fx_history(db_session, provider=provider, today=TODAY)
    assert db_session.query(FxDailyRate).filter_by(currency=Currency.ILS).count() == 2
    assert db_session.query(FxDailyRate).filter_by(currency=Currency.EUR).count() == 0
    assert any("EUR" in r.getMessage() and "failed" in r.getMessage() for r in caplog.records)
