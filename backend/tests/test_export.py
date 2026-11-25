"""Tests for the read-only positions export consumed by trender."""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.database import get_db
from app.models.asset import Currency
from app.models.holding import HoldingLot
from app.models.watchlist import Watchlist, WatchlistItem
from tests.factories import make_asset

TOKEN = "s3cret"


def client_for(db_session, monkeypatch, token=TOKEN):
    from app.routers import export as export_router_module

    monkeypatch.setattr(settings, "export_token", token)
    app = FastAPI()
    app.include_router(export_router_module.router, prefix="/api/export")
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def get_export(db_session, monkeypatch):
    c = client_for(db_session, monkeypatch)
    return c.get("/api/export/positions", headers={"X-Export-Token": TOKEN})


def add_lot(db, asset, quantity, avg_cost, account="A", cost_currency=Currency.USD,
            purchase_date=datetime(2025, 1, 1, tzinfo=timezone.utc)):
    lot = HoldingLot(
        id=str(uuid.uuid4()),
        asset_id=asset.id,
        quantity=quantity,
        avg_cost=avg_cost,
        cost_currency=cost_currency,
        account_name=account,
        purchase_date=purchase_date,
    )
    db.add(lot)
    db.commit()
    return lot


def add_to_watchlist(db, asset, name):
    wl = db.query(Watchlist).filter(Watchlist.name == name).first()
    if wl is None:
        wl = Watchlist(id=str(uuid.uuid4()), name=name)
        db.add(wl)
        db.commit()
    db.add(WatchlistItem(id=str(uuid.uuid4()), watchlist_id=wl.id, asset_id=asset.id))
    db.commit()


def test_disabled_when_token_empty(db_session, monkeypatch):
    c = client_for(db_session, monkeypatch, token="")
    r = c.get("/api/export/positions", headers={"X-Export-Token": "anything"})
    assert r.status_code == 503
    assert r.json() == {"detail": "export disabled"}


def test_rejects_missing_and_wrong_token(db_session, monkeypatch):
    c = client_for(db_session, monkeypatch)
    assert c.get("/api/export/positions").status_code == 401
    assert c.get("/api/export/positions", headers={"X-Export-Token": "nope"}).status_code == 401


def test_held_asset_across_accounts(db_session, monkeypatch):
    a = make_asset(db_session, "HELD")
    add_lot(db_session, a, 10, 100, account="A", purchase_date=datetime(2025, 3, 4, tzinfo=timezone.utc))
    add_lot(db_session, a, 30, 200, account="B", purchase_date=datetime(2024, 6, 1, tzinfo=timezone.utc))
    body = get_export(db_session, monkeypatch).json()
    assert len(body["assets"]) == 1
    asset = body["assets"][0]
    assert asset["held"] is True
    assert asset["total_quantity"] == 40
    assert asset["avg_cost"] == 175.0
    assert asset["avg_cost_currency"] == "USD"
    assert asset["cost_basis_complete"] is True
    assert asset["cost_basis_issue"] is None
    assert asset["first_purchase_date"] == "2024-06-01"
    assert asset["asset_type"] == "stock"
    assert asset["market_region"] == "US"
    assert asset["currency"] == "USD"


def test_mixed_cost_currency_gives_unknown_cost(db_session, monkeypatch):
    a = make_asset(db_session, "MIX")
    add_lot(db_session, a, 10, 100)
    add_lot(db_session, a, 5, 90, cost_currency=Currency.EUR)
    asset = get_export(db_session, monkeypatch).json()["assets"][0]
    assert asset["avg_cost"] is None
    assert asset["avg_cost_currency"] is None
    assert asset["cost_basis_complete"] is False
    assert asset["cost_basis_issue"] == "mixed_cost_currency"
    assert asset["total_quantity"] == 15
    assert asset["held"] is True


def test_cost_basis_uses_lot_currency_not_asset_currency(db_session, monkeypatch):
    a = make_asset(db_session, "ADR", currency=Currency.EUR)
    add_lot(db_session, a, 10, 100, cost_currency=Currency.USD)
    add_lot(db_session, a, 10, 120, account="B", cost_currency=Currency.USD)
    asset = get_export(db_session, monkeypatch).json()["assets"][0]
    assert asset["currency"] == "EUR"
    assert asset["avg_cost"] == 110.0
    assert asset["avg_cost_currency"] == "USD"
    assert asset["cost_basis_complete"] is True
    assert asset["cost_basis_issue"] is None


def test_zero_quantity_lots_ignored(db_session, monkeypatch):
    a = make_asset(db_session, "ZERO")
    add_lot(db_session, a, 0, 100)
    asset = get_export(db_session, monkeypatch).json()["assets"][0]
    assert asset["held"] is False
    assert asset["total_quantity"] is None
    assert asset["avg_cost"] is None
    assert asset["avg_cost_currency"] is None
    assert asset["first_purchase_date"] is None
    assert asset["cost_basis_complete"] is False
    assert asset["cost_basis_issue"] is None


def test_watch_only_asset(db_session, monkeypatch):
    a = make_asset(db_session, "WATCH")
    add_to_watchlist(db_session, a, "Core")
    asset = get_export(db_session, monkeypatch).json()["assets"][0]
    assert asset["held"] is False
    assert asset["watchlists"] == ["Core"]
    assert asset["total_quantity"] is None


def test_asset_in_two_watchlists_and_held(db_session, monkeypatch):
    a = make_asset(db_session, "BOTH")
    add_lot(db_session, a, 1, 10)
    add_to_watchlist(db_session, a, "Tech")
    add_to_watchlist(db_session, a, "Core")
    assets = get_export(db_session, monkeypatch).json()["assets"]
    assert len(assets) == 1
    assert assets[0]["watchlists"] == ["Core", "Tech"]
    assert assets[0]["held"] is True


def test_unrelated_asset_excluded(db_session, monkeypatch):
    make_asset(db_session, "LONELY")
    b = make_asset(db_session, "KEEP")
    add_to_watchlist(db_session, b, "Core")
    symbols = [x["symbol"] for x in get_export(db_session, monkeypatch).json()["assets"]]
    assert symbols == ["KEEP"]


def test_envelope(db_session, monkeypatch):
    b = make_asset(db_session, "BBB")
    a = make_asset(db_session, "AAA")
    add_lot(db_session, a, 1, 10)
    add_lot(db_session, b, 1, 10)
    r = get_export(db_session, monkeypatch)
    body = r.json()
    assert set(body) == {"schema_version", "generated_at", "asset_count", "assets"}
    assert body["schema_version"] == 1
    assert body["asset_count"] == len(body["assets"]) == 2
    assert [x["symbol"] for x in body["assets"]] == ["AAA", "BBB"]
    assert set(body["assets"][0]) == {
        "navigator_asset_id", "symbol", "name", "exchange", "asset_type",
        "market_region", "currency", "held", "total_quantity", "avg_cost",
        "avg_cost_currency", "cost_basis_complete", "cost_basis_issue",
        "first_purchase_date", "watchlists",
    }
    assert "account_name" not in r.text
    assert "tags" not in r.text


def test_no_provider_or_fx_calls(db_session, monkeypatch):
    from app.services import fx, market_data

    def boom(*a, **k):
        raise AssertionError("must not be called")

    for name in dir(market_data.MarketDataService):
        if not name.startswith("__") and callable(getattr(market_data.MarketDataService, name)):
            monkeypatch.setattr(market_data.MarketDataService, name, boom)
    monkeypatch.setattr(fx.FxService, "get_rate", boom)

    a = make_asset(db_session, "AAA")
    add_lot(db_session, a, 1, 10, cost_currency=Currency.EUR)
    assert get_export(db_session, monkeypatch).status_code == 200
