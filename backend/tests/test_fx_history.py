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
