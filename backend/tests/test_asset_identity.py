import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import get_db
from app.models.asset import Asset
from app.providers.yahoo import YahooFinanceProvider
from app.routers import alerts as alerts_router
from app.routers import assets as assets_router


@pytest.fixture()
def api(db_session):
    app = FastAPI()
    app.include_router(assets_router.router, prefix="/api/assets")
    app.include_router(alerts_router.router, prefix="/api/alerts")
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def _meta(monkeypatch, currency, venue, full=None, calls=None):
    async def fake(self, symbol):
        if calls is not None:
            calls.append(symbol)
        return {
            "currency": currency,
            "unitFactor": 1.0,
            "exchangeName": venue,
            "fullExchangeName": full,
            "instrumentType": "EQUITY",
        }

    monkeypatch.setattr(YahooFinanceProvider, "get_chart_meta", fake)


def _body(symbol="TEVA.TA", **kw):
    body = {"symbol": symbol, "name": "Teva", "asset_type": "stock",
            "currency": "USD", "market_region": "US"}
    body.update(kw)
    return body


def test_create_tase_asset_derives_identity(api, monkeypatch):
    _meta(monkeypatch, "ILS", "TLV", "Tel Aviv")
    r = api.post("/api/assets/", json=_body())
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["currency"] == "ILS"
    assert d["marketRegion"] == "IL"
    assert d["exchange"] == "Tel Aviv"


def test_create_london_asset_from_pence(api, monkeypatch):
    _meta(monkeypatch, "GBP", "LSE", "LSE")
    r = api.post("/api/assets/", json=_body("VOD.L"))
    assert r.status_code == 201, r.text
    assert r.json()["currency"] == "GBP"
    assert r.json()["marketRegion"] == "EU"


def test_create_asset_unknown_venue_keeps_client_region(api, monkeypatch):
    _meta(monkeypatch, "USD", "XYZ", "Some Exchange")
    r = api.post("/api/assets/", json=_body("ABC", market_region="EU"))
    assert r.status_code == 201, r.text
    assert r.json()["marketRegion"] == "EU"


def test_create_asset_unsupported_currency_422(api, db_session, monkeypatch):
    _meta(monkeypatch, "BRL", "SAO", "Sao Paulo")
    r = api.post("/api/assets/", json=_body("PETR4.SA"))
    assert r.status_code == 422
    assert r.json()["detail"] == "unsupported currency BRL for PETR4.SA"
    assert db_session.query(Asset).count() == 0


def test_create_asset_yahoo_down_503(api, db_session, monkeypatch):
    async def boom(self, symbol):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(YahooFinanceProvider, "get_chart_meta", boom)
    r = api.post("/api/assets/", json=_body())
    assert r.status_code == 503
    assert r.json()["detail"] == "couldn't verify symbol, try again"
    assert db_session.query(Asset).count() == 0


def test_create_asset_symbol_not_found_404(api, db_session, monkeypatch):
    async def none(self, symbol):
        return None

    monkeypatch.setattr(YahooFinanceProvider, "get_chart_meta", none)
    r = api.post("/api/assets/", json=_body("NOPE"))
    assert r.status_code == 404
    assert r.json()["detail"] == "symbol not found"
    assert db_session.query(Asset).count() == 0


def test_create_crypto_defaults_usd(api, monkeypatch):
    calls = []
    _meta(monkeypatch, "ILS", "TLV", calls=calls)
    r = api.post("/api/assets/", json={"symbol": "BTC", "name": "Bitcoin",
                                       "asset_type": "crypto"})
    assert r.status_code == 201, r.text
    assert r.json()["currency"] == "USD"
    assert r.json()["marketRegion"] == "US"
    assert calls == []


def test_create_other_requires_currency(api):
    r = api.post("/api/assets/", json={"symbol": "GOLDBAR", "name": "Gold",
                                       "asset_type": "other"})
    assert r.status_code == 422
    assert r.json()["detail"] == "currency is required for other assets"


def test_alert_by_symbol_creates_tase_asset(api, db_session, monkeypatch):
    _meta(monkeypatch, "ILS", "TLV", "Tel Aviv")
    r = api.post("/api/alerts/", json={"symbol": "TEVA.TA", "rule": "price_above",
                                       "threshold": 50})
    assert r.status_code in (200, 201), r.text
    a = db_session.query(Asset).filter(Asset.symbol == "TEVA.TA").one()
    assert a.currency == "ILS"
    assert a.market_region == "IL"
    assert a.exchange == "Tel Aviv"
