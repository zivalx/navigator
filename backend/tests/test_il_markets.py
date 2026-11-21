"""Israel market support: Tel Aviv indices as assets, region=IL movers,
TA indicators and readable venue names in search."""
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import get_db
from app.models.asset import Asset, AssetType, Currency, MarketRegion
from app.providers.yahoo import YahooFinanceProvider
from app.routers import assets as assets_router
from app.routers import markets as markets_router
from app.schemas import asset as asset_schemas
from app.services.indicators import REGISTRY_BY_KEY
from app.services.market_data import MarketDataService


class FakeCache:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ttl=None):
        self.store[key] = value


@pytest.fixture()
def fake_cache(monkeypatch):
    c = FakeCache()
    monkeypatch.setattr("app.services.market_data.cache.get", c.get)
    monkeypatch.setattr("app.services.market_data.cache.set", c.set)
    return c


def _quote(symbol, pct, price=10.0, currency="ILS"):
    return {"symbol": symbol, "price": price, "change": 0.1, "changePercent": pct,
            "currency": currency, "unitFactor": 0.01, "source": "yahoo"}


# --- index asset type -----------------------------------------------------

def test_index_asset_type_exists():
    assert AssetType("index") is AssetType.INDEX
    assert asset_schemas.AssetType("index") is asset_schemas.AssetType.INDEX


@pytest.fixture()
def api(db_session):
    app = FastAPI()
    app.include_router(assets_router.router, prefix="/api/assets")
    app.include_router(markets_router.router, prefix="/api/markets")
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def test_create_tel_aviv_index_derives_identity(api, monkeypatch):
    async def meta(self, symbol):
        return {"currency": "ILS", "unitFactor": 1.0, "exchangeName": "TLV",
                "fullExchangeName": "Tel Aviv", "instrumentType": "INDEX"}

    monkeypatch.setattr(YahooFinanceProvider, "get_chart_meta", meta)
    r = api.post("/api/assets/", json={
        "symbol": "TA35.TA", "name": "TA-35", "asset_type": "index",
        "exchange": "TLV", "provider_ids": {},
    })
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["assetType"] == "index"
    assert d["currency"] == "ILS"
    assert d["marketRegion"] == "IL"
    assert d["exchange"] == "Tel Aviv"


@pytest.mark.asyncio
async def test_index_asset_is_quoted_by_yahoo(db_session, fake_cache, monkeypatch):
    db_session.add(Asset(
        id=str(uuid.uuid4()), symbol="^TA125.TA", name="TA-125", exchange="Tel Aviv",
        currency=Currency.ILS, asset_type=AssetType.INDEX, market_region=MarketRegion.IL,
    ))
    db_session.commit()

    async def yahoo_quote(self, symbol):
        return _quote(symbol, 0.5, price=3752.65)

    monkeypatch.setattr(YahooFinanceProvider, "get_quote", yahoo_quote)
    service = MarketDataService(db_session)
    q = await service.get_quote("^TA125.TA")
    assert q["source"] == "yahoo"
    assert q["price"] == 3752.65
    assert q["currency"] == "ILS"


# --- region=IL movers -------------------------------------------------------

@pytest.mark.asyncio
async def test_yahoo_il_movers_split_and_named(monkeypatch):
    seen = []

    async def quotes(self, symbols):
        seen.extend(symbols)
        return {
            "LUMI.TA": _quote("LUMI.TA", 2.5, price=71.67),
            "TEVA.TA": _quote("TEVA.TA", -1.2, price=132.1),
            "NICE.TA": _quote("NICE.TA", 0.0),
            "ESLT.TA": _quote("ESLT.TA", 4.0, price=2250.6),
            "ICL.TA": _quote("ICL.TA", -3.0),
        }

    monkeypatch.setattr(YahooFinanceProvider, "get_quotes", quotes)
    provider = YahooFinanceProvider()
    try:
        both = await provider.get_il_movers(10)
    finally:
        await provider.close()

    assert all(s.endswith(".TA") for s in seen)
    assert [q["symbol"] for q in both["gainers"]] == ["ESLT.TA", "LUMI.TA"]
    assert [q["symbol"] for q in both["losers"]] == ["ICL.TA", "TEVA.TA"]
    assert both["gainers"][0]["name"] == "Elbit Systems"
    # Prices come through in ILS major units, untouched.
    assert both["gainers"][1]["price"] == 71.67
    assert both["gainers"][1]["currency"] == "ILS"


@pytest.mark.asyncio
async def test_il_movers_use_yahoo_only_and_own_cache(db_session, fake_cache, monkeypatch):
    calls = []

    async def il_movers(self, limit):
        calls.append(limit)
        return {"gainers": [_quote("LUMI.TA", 2.0)], "losers": [_quote("TEVA.TA", -1.0)]}

    async def us_movers(*a, **kw):
        raise AssertionError("US movers must not be used for region=IL")

    monkeypatch.setattr(YahooFinanceProvider, "get_il_movers", il_movers)
    service = MarketDataService(db_session)
    monkeypatch.setattr(service, "_fetch_and_cache_movers", us_movers)

    gainers = await service.get_top_gainers(5, "IL")
    losers = await service.get_top_losers(5, "il")
    assert [q["symbol"] for q in gainers] == ["LUMI.TA"]
    assert [q["symbol"] for q in losers] == ["TEVA.TA"]
    assert calls == [20]  # losers came from the cache filled by the first call
    assert "movers:IL:gainers" in fake_cache.store
    assert "movers:gainers" not in fake_cache.store


@pytest.mark.asyncio
async def test_il_movers_cache_empty_side(db_session, fake_cache, monkeypatch):
    calls = []

    async def il_movers(self, limit):
        calls.append(limit)
        return {"gainers": [_quote("LUMI.TA", 2.0)], "losers": []}

    monkeypatch.setattr(YahooFinanceProvider, "get_il_movers", il_movers)
    service = MarketDataService(db_session)
    assert await service.get_top_losers(5, "IL") == []
    assert await service.get_top_losers(5, "IL") == []
    assert calls == [20]  # an empty side is a valid answer, not a cache miss


@pytest.mark.asyncio
async def test_il_movers_yahoo_down_returns_empty(db_session, fake_cache, monkeypatch):
    async def boom(self, limit):
        raise RuntimeError("down")

    monkeypatch.setattr(YahooFinanceProvider, "get_il_movers", boom)
    service = MarketDataService(db_session)
    assert await service.get_top_gainers(5, "IL") == []
    assert "movers:IL:gainers" not in fake_cache.store


@pytest.mark.asyncio
async def test_us_movers_keep_their_cache_key(db_session, fake_cache):
    fake_cache.store["movers:gainers"] = [_quote("AAPL", 1.0, currency="USD")]
    service = MarketDataService(db_session)
    gainers = await service.get_top_gainers(5, "US")
    assert [q["symbol"] for q in gainers] == ["AAPL"]


def test_movers_endpoint_passes_region(api, fake_cache):
    fake_cache.store["movers:IL:gainers"] = [_quote("LUMI.TA", 2.0)]
    r = api.get("/api/markets/movers/gainers?region=IL&limit=5")
    assert r.status_code == 200
    assert [q["symbol"] for q in r.json()] == ["LUMI.TA"]


# --- indicators and search -------------------------------------------------

def test_tel_aviv_indicators_registered():
    assert REGISTRY_BY_KEY["ta35"]["symbol"] == "TA35.TA"
    assert REGISTRY_BY_KEY["ta125"]["symbol"] == "^TA125.TA"
    for key in ("ta35", "ta125"):
        assert REGISTRY_BY_KEY[key]["category"] == "index"
        assert REGISTRY_BY_KEY[key]["unit"] == "points"


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_search_returns_readable_exchange_name(monkeypatch):
    payload = {"quotes": [
        {"symbol": "TEVA", "quoteType": "EQUITY", "exchange": "NYQ", "exchDisp": "NYSE",
         "shortname": "Teva Pharmaceutical Industries"},
        {"symbol": "TEVA.TA", "quoteType": "EQUITY", "exchange": "TLV", "exchDisp": "Tel Aviv",
         "shortname": "TEVA PHARMA IND"},
        {"symbol": "TA35.TA", "quoteType": "INDEX", "exchange": "TLV", "shortname": "TA-35"},
    ]}
    provider = YahooFinanceProvider()

    async def get(url, params=None, headers=None):
        return _Resp(payload)

    monkeypatch.setattr(provider.client, "get", get)
    try:
        results = await provider.search("TEVA")
    finally:
        await provider.close()

    by_symbol = {r["symbol"]: r for r in results}
    assert by_symbol["TEVA"]["exchange"] == "NYQ"
    assert by_symbol["TEVA"]["exchange_name"] == "NYSE"
    assert by_symbol["TEVA.TA"]["exchange_name"] == "Tel Aviv"
    assert by_symbol["TA35.TA"]["asset_type"] == "index"
    assert by_symbol["TA35.TA"]["exchange_name"] is None
