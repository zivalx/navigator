"""Shared model factories and patch helpers for alert tests."""
import uuid

from app.models.alert import AlertIntent, AlertRule, PriceAlert
from app.models.asset import Asset, AssetType, Currency, MarketRegion
from app.services.market_data import MarketDataService


def make_asset(db, symbol="AAA", currency=Currency.USD):
    asset = Asset(
        id=str(uuid.uuid4()),
        symbol=symbol,
        name=f"{symbol} Inc.",
        exchange="NASDAQ",
        currency=currency,
        asset_type=AssetType.STOCK,
        market_region=MarketRegion.US,
    )
    db.add(asset)
    db.commit()
    return asset


def make_alert(
    db,
    asset,
    rule=AlertRule.PRICE_BELOW,
    threshold=100.0,
    is_active=True,
    triggered_at=None,
    triggered_price=None,
    acknowledged_at=None,
    note=None,
):
    alert = PriceAlert(
        id=str(uuid.uuid4()),
        asset_id=asset.id,
        rule=rule,
        threshold=threshold,
        note=note,
        is_active=is_active,
        triggered_at=triggered_at,
        triggered_price=triggered_price,
        acknowledged_at=acknowledged_at,
    )
    db.add(alert)
    db.commit()
    return alert


def make_tsl(db, asset, trail_percent=None, trail_amount=None, hwm=100.0, **kwargs):
    alert = PriceAlert(
        id=str(uuid.uuid4()),
        asset_id=asset.id,
        rule=AlertRule.TRAILING_STOP,
        intent=AlertIntent.SELL,
        trail_percent=trail_percent,
        trail_amount=trail_amount,
        high_water_mark=hwm,
        is_active=kwargs.pop("is_active", True),
        **kwargs,
    )
    db.add(alert)
    db.commit()
    return alert


def reload_alert(db, alert_id):
    """Re-query an alert by id.

    evaluate_price_alerts() closes its session when done, which detaches
    (and expires) ORM instances created via the factories — capture the id
    string before invoking evaluate, then reload to inspect post-evaluation
    state.
    """
    return db.query(PriceAlert).filter(PriceAlert.id == alert_id).first()


def patch_quote(monkeypatch, price):
    async def fake_get_quote(self, symbol, asset_type=None):
        return {"symbol": symbol.upper(), "price": price, "currency": "USD"}

    monkeypatch.setattr(MarketDataService, "get_quote", fake_get_quote)


def patch_quotes(monkeypatch, quotes_by_symbol):
    async def fake_get_quotes(self, symbols):
        return {s: quotes_by_symbol[s] for s in symbols if s in quotes_by_symbol}

    monkeypatch.setattr(MarketDataService, "get_quotes", fake_get_quotes)
