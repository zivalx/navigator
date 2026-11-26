"""Live FX: Yahoo {CCY}=X first, then ExchangeRate-API, then the DB. All offline."""
import uuid
from datetime import datetime

import httpx
import pytest

from app.config import settings
from app.models.asset import Currency
from app.models.price import FxRate
from app.providers.yahoo import YahooFinanceProvider
from app.services import fx as fx_module
from app.services.fx import FxService

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"
ERAPI = "exchangerate-api.com"


def _yahoo_body(price):
    return {"chart": {"result": [{"meta": {"currency": "ILS", "regularMarketPrice": price}}]}}


class _Net:
    """Routes httpx.AsyncClient.get by URL; records calls; fails what isn't configured."""

    def __init__(self, yahoo=None, erapi=None):
        self.yahoo = yahoo or {}  # code -> per-USD price (or Exception)
        self.erapi = erapi  # json body for exchangerate-api, or None to fail
        self.calls = []

    async def get(self, client, url, params=None, headers=None, **kw):
        self.calls.append((url, params))
        req = httpx.Request("GET", url)
        if url.startswith(YAHOO):
            code = url[len(YAHOO):].removesuffix("=X")
            value = self.yahoo.get(code)
            if value is None:
                return httpx.Response(404, json={"chart": {"result": None}}, request=req)
            if isinstance(value, Exception):
                raise value
            return httpx.Response(200, json=_yahoo_body(value), request=req)
        if ERAPI in url and self.erapi is not None:
            return httpx.Response(200, json=self.erapi, request=req)
        raise httpx.ConnectError("offline", request=req)

    def hit(self, needle):
        return [u for u, _ in self.calls if needle in u]


@pytest.fixture()
def net(monkeypatch):
    n = _Net()

    async def fake_get(self, url, *a, **kw):
        return await n.get(self, url, *a, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(settings, "exchangerate_api_key", None)
    return n


@pytest.fixture()
def fake_cache(monkeypatch):
    store, sets = {}, []

    async def get(key):
        return store.get(key)

    async def set_(key, value, ttl=60):
        sets.append((key, value, ttl))
        store[key] = value

    monkeypatch.setattr(fx_module.cache, "get", get)
    monkeypatch.setattr(fx_module.cache, "set", set_)
    return store, sets


# --- provider ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_fx_quote_reads_regular_market_price(net):
    net.yahoo = {"ILS": 3.71}
    provider = YahooFinanceProvider()
    assert await provider.get_fx_quote("ILS") == 3.71
    url, params = net.calls[0]
    assert url == YahooFinanceProvider.QUERY_URL.format(symbol="ILS=X")
    assert params == {"interval": "1d", "range": "1d"}


@pytest.mark.asyncio
@pytest.mark.parametrize("price", [0, -1.0, None, "3.7"])
async def test_get_fx_quote_rejects_bad_price(net, price):
    net.yahoo = {"ILS": price} if price is not None else {}
    provider = YahooFinanceProvider()
    with pytest.raises((ValueError, httpx.HTTPError)):
        await provider.get_fx_quote("ILS")


@pytest.mark.asyncio
async def test_get_fx_quote_missing_meta_price(monkeypatch):
    async def fake_get(self, url, *a, **kw):
        return httpx.Response(200, json={"chart": {"result": [{"meta": {}}]}},
                              request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    with pytest.raises(ValueError):
        await YahooFinanceProvider().get_fx_quote("ILS")


# --- FxService.get_rate -----------------------------------------------------

@pytest.mark.asyncio
async def test_get_rate_prefers_yahoo(db_session, net):
    net.yahoo = {"ILS": 3.7}
    net.erapi = {"rates": {"ILS": 3.9}}
    svc = FxService(db_session)
    assert await svc.get_rate(Currency.USD, Currency.ILS) == pytest.approx(3.7)
    assert await svc.get_rate(Currency.ILS, Currency.USD) == pytest.approx(1 / 3.7)
    assert net.hit(ERAPI) == []
    # USD leg needs no request
    assert all("USD=X" not in u for u, _ in net.calls)
    await svc.close()


@pytest.mark.asyncio
async def test_get_rate_crosses_through_usd(db_session, net):
    net.yahoo = {"ILS": 3.7, "EUR": 0.925}
    svc = FxService(db_session)
    assert await svc.get_rate(Currency.EUR, Currency.ILS) == pytest.approx(3.7 / 0.925)
    assert len(net.hit("EUR=X")) == 1 and len(net.hit("ILS=X")) == 1
    await svc.close()


@pytest.mark.asyncio
async def test_get_rate_falls_back_to_exchangerate_api(db_session, net):
    net.yahoo = {"ILS": httpx.ConnectError("down")}
    net.erapi = {"rates": {"ILS": 3.9}}
    svc = FxService(db_session)
    assert await svc.get_rate(Currency.USD, Currency.ILS) == pytest.approx(3.9)
    assert len(net.hit(ERAPI)) == 1
    await svc.close()


@pytest.mark.asyncio
async def test_get_rate_falls_back_when_one_cross_leg_missing(db_session, net):
    net.yahoo = {"ILS": 3.7}  # EUR=X 404s
    net.erapi = {"rates": {"ILS": 4.0}}
    svc = FxService(db_session)
    assert await svc.get_rate(Currency.EUR, Currency.ILS) == pytest.approx(4.0)
    await svc.close()


@pytest.mark.asyncio
async def test_get_rate_falls_back_to_db_when_all_providers_fail(db_session, net):
    db_session.add(FxRate(id=str(uuid.uuid4()), base_currency=Currency.USD,
                          quote_currency=Currency.ILS, rate=3.6, timestamp=datetime.now()))
    db_session.commit()
    svc = FxService(db_session)
    assert await svc.get_rate(Currency.USD, Currency.ILS) == pytest.approx(3.6)
    assert await svc.get_rate(Currency.ILS, Currency.USD) == pytest.approx(1 / 3.6)
    await svc.close()


@pytest.mark.asyncio
async def test_get_rate_raises_when_nothing_available(db_session, net):
    svc = FxService(db_session)
    with pytest.raises(ValueError):
        await svc.get_rate(Currency.USD, Currency.ILS)
    await svc.close()


@pytest.mark.asyncio
async def test_get_rate_stores_yahoo_rate_in_db(db_session, net):
    net.yahoo = {"ILS": 3.7}
    svc = FxService(db_session)
    await svc.get_rate(Currency.USD, Currency.ILS)
    row = db_session.query(FxRate).one()
    assert (row.base_currency, row.quote_currency) == (Currency.USD, Currency.ILS)
    assert row.rate == pytest.approx(3.7)
    await svc.close()


@pytest.mark.asyncio
async def test_get_rate_keeps_cache_key_and_ttl(db_session, net, fake_cache):
    store, sets = fake_cache
    net.yahoo = {"ILS": 3.7}
    svc = FxService(db_session)
    assert await svc.get_rate(Currency.USD, Currency.ILS) == pytest.approx(3.7)
    assert sets == [("fx:USD:ILS", pytest.approx(3.7), settings.fx_cache_ttl)]

    net.calls.clear()
    assert await svc.get_rate(Currency.USD, Currency.ILS) == pytest.approx(3.7)
    assert net.calls == []  # served from cache
    await svc.close()


@pytest.mark.asyncio
async def test_close_closes_yahoo_client(db_session, net):
    net.yahoo = {"ILS": 3.7}
    svc = FxService(db_session)
    await svc.get_rate(Currency.USD, Currency.ILS)
    yahoo_client = svc._yahoo.client
    await svc.close()
    assert yahoo_client.is_closed
