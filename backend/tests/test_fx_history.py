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
