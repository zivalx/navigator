"""Endpoint tests for GET /api/markets/indicators.

Mounts just the markets router on a bare FastAPI app (no lifespan, no real
DB/Redis) and monkeypatches the provider *classes* so every IndicatorsService
instance the endpoint creates picks up the fakes.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.providers.alternative_me import AlternativeMeFearGreedProvider
from app.providers.cnn import CNNFearGreedProvider
from app.providers.yahoo import YahooFinanceProvider
from app.routers import markets
from app.services import breadth as breadth_service
from app.services.indicators import REGISTRY


YAHOO_QUOTES = {
    "^VIX": {"symbol": "^VIX", "price": 18.42, "change": -0.35, "changePercent": -1.87},
    "^GSPC": {"symbol": "^GSPC", "price": 5000.0, "change": 12.5, "changePercent": 0.25},
    "^TNX": {"symbol": "^TNX", "price": 43.2, "change": -0.5, "changePercent": -1.14},
}


async def fake_get_quote(self, symbol):
    symbol = symbol.upper()
    if symbol not in YAHOO_QUOTES:
        raise ValueError(f"No Yahoo data for {symbol}")
    return dict(YAHOO_QUOTES[symbol])


async def fake_cnn_get_fear_greed(self):
    return {"value": 38.0, "rating": "fear", "change": -4.0}


async def fake_alt_me_get_fear_greed(self):
    return {"value": 31.0, "rating": "fear", "change": -2.0}


async def fake_get_cached_breadth(key):
    if key == "s5fi":
        return {"value": 63.5, "previous": 60.0, "computed_at": "2026-07-23T00:00:00Z"}
    return None  # s5th: cold cache


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(markets.router, prefix="/api/markets")
    return TestClient(app)


@pytest.fixture(autouse=True)
def patch_providers(monkeypatch):
    monkeypatch.setattr(YahooFinanceProvider, "get_quote", fake_get_quote)
    monkeypatch.setattr(CNNFearGreedProvider, "get_fear_greed", fake_cnn_get_fear_greed)
    monkeypatch.setattr(AlternativeMeFearGreedProvider, "get_fear_greed", fake_alt_me_get_fear_greed)
    monkeypatch.setattr(breadth_service, "get_cached_breadth", fake_get_cached_breadth)


def test_indicators_happy_path_shape(client):
    response = client.get(
        "/api/markets/indicators",
        params={"keys": "fear_greed_stocks,fear_greed_crypto,vix,sp500,us10y,s5fi,s5th"},
    )
    assert response.status_code == 200
    body = response.json()

    assert "as_of" in body
    assert body["as_of"].endswith("Z")

    indicators = {ind["key"]: ind for ind in body["indicators"]}
    assert set(indicators) == {
        "fear_greed_stocks", "fear_greed_crypto", "vix", "sp500", "us10y", "s5fi", "s5th"
    }

    fg = indicators["fear_greed_stocks"]
    assert fg["label"] == "Fear & Greed"
    assert fg["category"] == "sentiment"
    assert fg["value"] == 38.0
    assert fg["rating"] == "fear"
    assert fg["change"] == -4.0
    assert fg["source"] == "cnn"
    assert fg["error"] is None

    crypto = indicators["fear_greed_crypto"]
    assert crypto["value"] == 31.0
    assert crypto["rating"] == "fear"
    assert crypto["source"] == "alternative.me"

    vix = indicators["vix"]
    assert vix["value"] == 18.42
    assert vix["change"] == -0.35
    assert vix["change_pct"] == -1.87
    assert vix["unit"] == "points"
    assert vix["rating"] is None

    us10y = indicators["us10y"]
    assert us10y["value"] == pytest.approx(4.32)
    assert us10y["change"] == pytest.approx(-0.05)
    assert us10y["unit"] == "%"

    s5fi = indicators["s5fi"]
    assert s5fi["category"] == "breadth"
    assert s5fi["value"] == 63.5
    assert s5fi["change"] == pytest.approx(3.5)
    assert s5fi["error"] is None

    s5th = indicators["s5th"]
    assert s5th["value"] is None
    assert s5th["error"] == "breadth not yet computed"


def test_indicators_keys_filtering_ignores_unknown_keys(client):
    response = client.get(
        "/api/markets/indicators", params={"keys": "vix,not_a_real_key,,sp500"}
    )
    assert response.status_code == 200
    body = response.json()
    assert [ind["key"] for ind in body["indicators"]] == ["vix", "sp500"]


def test_indicators_empty_keys_returns_all(client):
    response = client.get("/api/markets/indicators", params={"keys": ""})
    assert response.status_code == 200
    body = response.json()
    assert len(body["indicators"]) == len(REGISTRY)


def test_indicators_omitted_keys_returns_all(client):
    response = client.get("/api/markets/indicators")
    assert response.status_code == 200
    body = response.json()
    assert len(body["indicators"]) == len(REGISTRY)
    assert {ind["key"] for ind in body["indicators"]} == {e["key"] for e in REGISTRY}


def test_indicators_partial_failure_still_returns_200(client, monkeypatch):
    """One provider raising must not take down the whole response — the
    failing indicator gets value=null + error, everything else is fine."""

    async def flaky_get_quote(self, symbol):
        if symbol.upper() == "^VIX":
            raise ValueError("Yahoo is down")
        return await fake_get_quote(self, symbol)

    monkeypatch.setattr(YahooFinanceProvider, "get_quote", flaky_get_quote)

    response = client.get(
        "/api/markets/indicators", params={"keys": "vix,sp500,fear_greed_stocks"}
    )
    assert response.status_code == 200
    body = response.json()
    indicators = {ind["key"]: ind for ind in body["indicators"]}

    assert indicators["vix"]["value"] is None
    assert indicators["vix"]["error"] == "Yahoo is down"

    assert indicators["sp500"]["value"] == 5000.0
    assert indicators["sp500"]["error"] is None

    assert indicators["fear_greed_stocks"]["value"] == 38.0
    assert indicators["fear_greed_stocks"]["error"] is None
