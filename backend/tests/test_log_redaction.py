"""Provider error text must not carry API keys into logs."""
import logging

import httpx
import pytest

from app.services.log_redaction import redact_secrets
from app.services.market_data import MarketDataService


def _status_error(url: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", url)
    response = httpx.Response(403, request=request)
    return httpx.HTTPStatusError(
        f"Client error '403 Forbidden' for url '{url}'", request=request, response=response
    )


def test_redacts_url_query_strings():
    err = _status_error("https://finnhub.io/api/v1/quote?symbol=AAPL&token=sekrit123")
    text = redact_secrets(err)
    assert "sekrit123" not in text
    assert "https://finnhub.io/api/v1/quote" in text
    assert "403 Forbidden" in text


@pytest.mark.parametrize("param", ["token", "apiKey", "apikey", "api_key", "api_token"])
def test_redacts_known_key_params_outside_urls(param):
    text = redact_secrets(f"bad request: {param}=sekrit123&symbol=AAPL")
    assert "sekrit123" not in text
    assert "symbol=AAPL" in text


def test_redacts_configured_key_embedded_in_path(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "exchangerate_api_key", "pathkey987")
    text = redact_secrets("error for url 'https://v6.exchangerate-api.com/v6/pathkey987/pair/ILS/USD'")
    assert "pathkey987" not in text


def test_plain_text_unchanged():
    assert redact_secrets("No price data available for TEVA.TA") == (
        "No price data available for TEVA.TA"
    )


def _keyed_providers_fail(db, monkeypatch):
    async def cache_miss(*a, **k):
        return None

    monkeypatch.setattr("app.services.market_data.cache.get", cache_miss)
    monkeypatch.setattr("app.services.market_data.cache.set", cache_miss)

    service = MarketDataService(db)

    async def yahoo_fails(symbol):
        raise _status_error(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d")

    class _Finnhub:
        async def get_quote(self, symbol):
            raise _status_error(f"https://finnhub.io/api/v1/quote?symbol={symbol}&token=sekrit123")

    class _Polygon:
        async def get_quote(self, symbol):
            raise _status_error(f"https://api.polygon.io/v2/last/{symbol}?apiKey=polykey456")

    monkeypatch.setattr(service.yahoo, "get_quote", yahoo_fails)
    service.finnhub = _Finnhub()
    service.polygon = _Polygon()
    return service


SECRETS = ("sekrit123", "polykey456")


@pytest.mark.asyncio
async def test_quote_error_redacts_keys(db_session, monkeypatch):
    """The raised error reaches the portfolio's price_unavailable warning and
    the /quotes API body: it must carry no provider key."""
    service = _keyed_providers_fail(db_session, monkeypatch)
    with pytest.raises(ValueError) as exc:
        await service.get_quote("ZZZZ")
    for secret in SECRETS:
        assert secret not in str(exc.value)
    assert "finnhub.io/api/v1/quote" in str(exc.value)


@pytest.mark.asyncio
async def test_db_fallback_log_redacts_keys(db_session, monkeypatch, caplog):
    from datetime import datetime, timezone
    import uuid

    from app.models.price import PriceSnapshot
    from tests.factories import make_asset

    asset = make_asset(db_session, symbol="AAPL")
    db_session.add(PriceSnapshot(id=str(uuid.uuid4()), asset_id=asset.id, price=1.0,
                                 currency="USD", timestamp=datetime.now(timezone.utc),
                                 source="eod_snapshot"))
    db_session.commit()
    service = _keyed_providers_fail(db_session, monkeypatch)
    with caplog.at_level(logging.WARNING, logger="app.services.market_data"):
        quote = await service.get_quote("AAPL")
    assert quote["stale"] is True
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "FALLBACK [AAPL]" in logged
    for secret in SECRETS:
        assert secret not in logged


@pytest.mark.asyncio
async def test_price_unavailable_log_redacts_keys(db_session, monkeypatch, caplog):
    from app.models.asset import Currency
    from app.services.portfolio import PortfolioService
    from tests.factories import make_asset
    from tests.test_portfolio_unavailable import make_lot

    asset = make_asset(db_session, symbol="AAPL")
    make_lot(db_session, asset, 1, 1.0, Currency.USD)

    async def quote_fails(self, symbol, asset_type=None):
        raise _status_error("https://finnhub.io/api/v1/quote?symbol=AAPL&token=sekrit123")

    monkeypatch.setattr(MarketDataService, "get_quote", quote_fails)
    with caplog.at_level(logging.WARNING, logger="app.services.portfolio"):
        [h] = await PortfolioService(db_session, base_currency=Currency.USD).get_holdings_with_prices()
    assert h.unavailable_reason == "price_unavailable"
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "price_unavailable" in logged and "sekrit123" not in logged


def test_strips_url_userinfo():
    text = redact_secrets("could not connect to postgresql://navigator:hunter2@db:5432/navigator")
    assert "hunter2" not in text and "navigator:" not in text
    assert "postgresql://***@db:5432/navigator" in text


def test_strips_sqlalchemy_parameters_segment():
    text = redact_secrets(
        "(sqlite3.IntegrityError) UNIQUE failed\n"
        "[SQL: INSERT INTO portfolio_snapshots (nav) VALUES (?)]\n"
        "[parameters: ('x', 12345.678, 9.99)]\n"
        "(Background on this error at: https://sqlalche.me/e/20/gkpj)"
    )
    assert "12345.678" not in text and "9.99" not in text
    assert "[SQL: INSERT INTO portfolio_snapshots" in text and "Background on this error" in text


def test_strips_truncated_parameters_segment():
    assert "777.5" not in redact_secrets("boom [parameters: (1, 777.5, 'abc'")


def test_integrity_error_log_has_no_parameter_values(db_session, caplog):
    import uuid
    from datetime import date

    from sqlalchemy.exc import IntegrityError

    from app.models.portfolio_snapshot import PortfolioSnapshot

    def row():
        return PortfolioSnapshot(id=str(uuid.uuid4()), date=date(2026, 1, 2), nav=123456.789, net_flow=424242.5)

    db_session.add(row())
    db_session.commit()
    db_session.add(row())
    with pytest.raises(IntegrityError) as exc:
        db_session.commit()
    db_session.rollback()
    assert "123456.789" in str(exc.value)  # an unhidden engine does leak: the premise
    with caplog.at_level(logging.WARNING):
        logging.getLogger("t").warning("write failed: %s", redact_secrets(exc.value))
    assert "123456.789" not in caplog.text and "424242.5" not in caplog.text


def test_every_engine_hides_parameters():
    from app.database import engine
    from app.tasks.leader import PgAdvisoryLock

    assert engine.hide_parameters is True
    lock = PgAdvisoryLock("postgresql://u:p@127.0.0.1:1/x")
    assert lock._engine.hide_parameters is True


@pytest.mark.parametrize("raw,masked", [
    ("postgresql://nav:ab/cd@h/db", "postgresql://***@h/db"),
    ("redis://:p@ss@h", "redis://***@h"),
    ("connect to postgresql://nav:pw@h:5432/db failed", "connect to postgresql://***@h:5432/db failed"),
])
def test_url_userinfo_masked_up_to_the_last_at(raw, masked):
    text = redact_secrets(raw)
    assert text == masked
    assert "ab/cd" not in text and "p@ss" not in text
