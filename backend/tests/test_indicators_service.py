"""Unit tests for IndicatorsService — Yahoo yield scaling, breadth
cache-only serving, and per-indicator error isolation. All external
providers are mocked; no network calls.
"""
import pytest

from app.services import breadth as breadth_service
from app.services.indicators import IndicatorsService, REGISTRY, REGISTRY_BY_KEY


@pytest.fixture()
def service():
    svc = IndicatorsService()
    yield svc


@pytest.mark.asyncio
async def test_registry_has_expected_keys():
    expected = {
        "fear_greed_stocks", "fear_greed_crypto", "vix", "sp500", "nasdaq",
        "dow", "russell2000", "stoxx50", "dax", "smi", "nikkei", "us10y",
        "us30y", "s5fi", "s5th", "dxy", "gold", "oil_wti", "btc",
    }
    assert set(REGISTRY_BY_KEY.keys()) == expected
    assert len(REGISTRY) == len(expected)


@pytest.mark.asyncio
async def test_us10y_scales_value_and_change_by_one_tenth(service, monkeypatch):
    async def fake_get_quote(symbol):
        assert symbol == "^TNX"
        return {"symbol": "^TNX", "price": 43.2, "change": -0.5, "changePercent": -1.14}

    monkeypatch.setattr(service.yahoo, "get_quote", fake_get_quote)

    entry = REGISTRY_BY_KEY["us10y"]
    result = await service._get_one(entry)

    assert result["value"] == pytest.approx(4.32)
    assert result["change"] == pytest.approx(-0.05)
    assert result["unit"] == "%"
    # change_pct is scale-invariant — passed through unscaled.
    assert result["change_pct"] == pytest.approx(-1.14)
    assert result["error"] is None


@pytest.mark.asyncio
async def test_us30y_scales_value_and_change_by_one_tenth(service, monkeypatch):
    async def fake_get_quote(symbol):
        assert symbol == "^TYX"
        return {"symbol": "^TYX", "price": 44.8, "change": 1.2, "changePercent": 2.75}

    monkeypatch.setattr(service.yahoo, "get_quote", fake_get_quote)

    entry = REGISTRY_BY_KEY["us30y"]
    result = await service._get_one(entry)

    assert result["value"] == pytest.approx(4.48)
    assert result["change"] == pytest.approx(0.12)
    assert result["unit"] == "%"


@pytest.mark.asyncio
async def test_regular_index_is_not_scaled(service, monkeypatch):
    async def fake_get_quote(symbol):
        return {"symbol": symbol, "price": 5000.0, "change": 12.5, "changePercent": 0.25}

    monkeypatch.setattr(service.yahoo, "get_quote", fake_get_quote)

    entry = REGISTRY_BY_KEY["sp500"]
    result = await service._get_one(entry)

    assert result["value"] == 5000.0
    assert result["change"] == 12.5
    assert result["unit"] == "points"


@pytest.mark.asyncio
async def test_yahoo_failure_yields_null_value_and_error(service, monkeypatch):
    async def failing_get_quote(symbol):
        raise ValueError("Yahoo is down")

    monkeypatch.setattr(service.yahoo, "get_quote", failing_get_quote)

    entry = REGISTRY_BY_KEY["vix"]
    result = await service._get_one(entry)

    assert result["value"] is None
    assert result["error"] == "Yahoo is down"


@pytest.mark.asyncio
async def test_cnn_failure_yields_null_value_and_error(service, monkeypatch):
    async def failing_cnn():
        raise RuntimeError("CNN blocked the request")

    monkeypatch.setattr(service.cnn, "get_fear_greed", failing_cnn)

    entry = REGISTRY_BY_KEY["fear_greed_stocks"]
    result = await service._get_one(entry)

    assert result["value"] is None
    assert result["error"] == "CNN blocked the request"
    assert result["rating"] is None


@pytest.mark.asyncio
async def test_cnn_success_populates_rating_and_change(service, monkeypatch):
    async def fake_cnn():
        return {"value": 38.0, "rating": "fear", "change": -4.0}

    monkeypatch.setattr(service.cnn, "get_fear_greed", fake_cnn)

    entry = REGISTRY_BY_KEY["fear_greed_stocks"]
    result = await service._get_one(entry)

    assert result["value"] == 38.0
    assert result["rating"] == "fear"
    assert result["change"] == -4.0
    assert result["category"] == "sentiment"
    assert result["error"] is None


@pytest.mark.asyncio
async def test_breadth_cold_cache_returns_error_not_null_500(service, monkeypatch):
    async def cold_cache(key):
        return None

    monkeypatch.setattr(breadth_service, "get_cached_breadth", cold_cache)

    entry = REGISTRY_BY_KEY["s5fi"]
    result = await service._get_one(entry)

    assert result["value"] is None
    assert result["error"] == "breadth not yet computed"


@pytest.mark.asyncio
async def test_breadth_warm_cache_computes_change(service, monkeypatch):
    async def warm_cache(key):
        assert key == "s5th"
        return {"value": 63.5, "previous": 60.0, "computed_at": "2026-07-23T00:00:00Z"}

    monkeypatch.setattr(breadth_service, "get_cached_breadth", warm_cache)

    entry = REGISTRY_BY_KEY["s5th"]
    result = await service._get_one(entry)

    assert result["value"] == 63.5
    assert result["change"] == pytest.approx(3.5)
    assert result["change_pct"] is None
    assert result["error"] is None
