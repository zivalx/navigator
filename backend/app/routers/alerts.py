"""Price alerts / stop-loss API.

See docs/superpowers/specs/2026-07-23-price-alerts-design.md for the design.
Schemas live inline here (rather than in app/schemas/) to keep this feature
self-contained in the files it owns.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from ..database import get_db
from ..models.alert import AlertIntent, AlertRule, PriceAlert
from ..models.asset import Asset, AssetType, Currency, MarketRegion
from ..services.market_data import MarketDataService

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class PriceAlertCreate(BaseModel):
    asset_id: Optional[str] = Field(default=None, alias="assetId")
    symbol: Optional[str] = None
    rule: AlertRule
    threshold: Optional[float] = None
    intent: Optional[AlertIntent] = None
    trail_percent: Optional[float] = Field(default=None, alias="trailPercent")
    trail_amount: Optional[float] = Field(default=None, alias="trailAmount")
    note: Optional[str] = None

    class Config:
        populate_by_name = True

    @model_validator(mode="after")
    def _require_target(self):
        if not self.asset_id and not self.symbol:
            raise ValueError("Either assetId or symbol is required")
        return self

    @model_validator(mode="after")
    def _validate_rule_fields(self):
        if self.rule == AlertRule.TRAILING_STOP:
            if (self.trail_percent is None) == (self.trail_amount is None):
                raise ValueError(
                    "Trailing stop requires exactly one of trailPercent or trailAmount"
                )
            trail = self.trail_percent if self.trail_percent is not None else self.trail_amount
            if trail <= 0:
                raise ValueError("Trail must be positive")
            if self.trail_percent is not None and self.trail_percent >= 100:
                raise ValueError("trailPercent must be below 100")
            if self.threshold is not None:
                raise ValueError("Trailing stop alerts do not take a threshold")
        else:
            if self.threshold is None:
                raise ValueError("Threshold is required for price rules")
            if self.trail_percent is not None or self.trail_amount is not None:
                raise ValueError("Trail fields only apply to trailing stop alerts")
        return self


class PriceAlertUpdate(BaseModel):
    rule: Optional[AlertRule] = None
    threshold: Optional[float] = None
    intent: Optional[AlertIntent] = None
    trail_percent: Optional[float] = Field(default=None, alias="trailPercent")
    trail_amount: Optional[float] = Field(default=None, alias="trailAmount")
    note: Optional[str] = None
    is_active: Optional[bool] = Field(default=None, alias="isActive")

    class Config:
        populate_by_name = True


class PriceAlertOut(BaseModel):
    id: str
    asset_id: str = Field(alias="assetId")
    symbol: str
    name: str
    rule: AlertRule
    threshold: Optional[float] = None
    intent: Optional[AlertIntent] = None
    trail_percent: Optional[float] = Field(default=None, alias="trailPercent")
    trail_amount: Optional[float] = Field(default=None, alias="trailAmount")
    high_water_mark: Optional[float] = Field(default=None, alias="highWaterMark")
    current_stop_price: Optional[float] = Field(default=None, alias="currentStopPrice")
    note: Optional[str] = None
    is_active: bool = Field(alias="isActive")
    created_at: datetime = Field(alias="createdAt")
    triggered_at: Optional[datetime] = Field(default=None, alias="triggeredAt")
    triggered_price: Optional[float] = Field(default=None, alias="triggeredPrice")
    acknowledged_at: Optional[datetime] = Field(default=None, alias="acknowledgedAt")

    class Config:
        from_attributes = True
        populate_by_name = True
        use_enum_values = True


def _serialize(alert: PriceAlert) -> PriceAlertOut:
    return PriceAlertOut(
        id=alert.id,
        asset_id=alert.asset_id,
        symbol=alert.asset.symbol if alert.asset else "",
        name=alert.asset.name if alert.asset else "",
        rule=alert.rule,
        threshold=alert.threshold,
        intent=alert.intent,
        trail_percent=alert.trail_percent,
        trail_amount=alert.trail_amount,
        high_water_mark=alert.high_water_mark,
        current_stop_price=alert.stop_price,
        note=alert.note,
        is_active=alert.is_active,
        created_at=alert.created_at,
        triggered_at=alert.triggered_at,
        triggered_price=alert.triggered_price,
        acknowledged_at=alert.acknowledged_at,
    )


async def _resolve_asset(asset_id: Optional[str], symbol: Optional[str], db: Session) -> Asset:
    """Resolve the target asset for an alert, creating a minimal Asset
    record by symbol if one doesn't exist yet (mirrors the
    resolve-or-create convenience the frontend expects for symbol-based
    creation, per the price-alerts spec)."""
    if asset_id:
        asset = db.query(Asset).filter(Asset.id == asset_id).first()
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")
        return asset

    symbol_upper = symbol.strip().upper()
    if not symbol_upper:
        raise HTTPException(status_code=400, detail="Symbol cannot be empty")

    asset = db.query(Asset).filter(Asset.symbol == symbol_upper).first()
    if asset:
        return asset

    # Best-effort enrichment from live market data; fall back to sane
    # defaults if the quote can't be fetched (e.g. unknown/offline symbol).
    currency_value = Currency.USD
    try:
        market_service = MarketDataService(db)
        quote = await market_service.get_quote(symbol_upper)
        try:
            currency_value = Currency(quote.get("currency", "USD"))
        except ValueError:
            currency_value = Currency.USD
    except Exception:
        pass

    asset = Asset(
        id=str(uuid.uuid4()),
        symbol=symbol_upper,
        name=symbol_upper,
        currency=currency_value,
        asset_type=AssetType.STOCK,
        market_region=MarketRegion.US,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/", response_model=List[PriceAlertOut])
async def get_alerts(
    status: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    """List alerts, optionally filtered by ?status=active|triggered|unacknowledged."""
    if status is not None and status not in ("active", "triggered", "unacknowledged"):
        raise HTTPException(
            status_code=400,
            detail="status must be one of: active, triggered, unacknowledged",
        )

    query = db.query(PriceAlert)
    if status == "active":
        query = query.filter(PriceAlert.is_active.is_(True))
    elif status == "triggered":
        query = query.filter(PriceAlert.triggered_at.isnot(None))
    elif status == "unacknowledged":
        query = query.filter(
            PriceAlert.triggered_at.isnot(None),
            PriceAlert.acknowledged_at.is_(None),
        )

    alerts = query.order_by(PriceAlert.created_at.desc()).all()
    return [_serialize(a) for a in alerts]


async def _fetch_current_price(asset: Asset, db: Session) -> float:
    """Current quote price, needed to seed a trailing stop's high-water mark."""
    try:
        market_service = MarketDataService(db)
        quote = await market_service.get_quote(asset.symbol, asset.asset_type.value)
        price = quote.get("price")
    except Exception:
        price = None
    if price is None:
        raise HTTPException(
            status_code=400,
            detail=f"Could not fetch a current price for {asset.symbol} to start the trailing stop",
        )
    return price


@router.post("/", response_model=PriceAlertOut, status_code=201)
async def create_alert(alert_in: PriceAlertCreate, db: Session = Depends(get_db)):
    """Create a new alert, targeting an asset by assetId or by symbol
    (resolving/creating the asset if it doesn't exist yet)."""
    if alert_in.threshold is not None and alert_in.threshold <= 0:
        raise HTTPException(status_code=400, detail="Threshold must be positive")

    asset = await _resolve_asset(alert_in.asset_id, alert_in.symbol, db)

    is_tsl = alert_in.rule == AlertRule.TRAILING_STOP
    high_water_mark = await _fetch_current_price(asset, db) if is_tsl else None
    intent = alert_in.intent
    if intent is None and is_tsl:
        intent = AlertIntent.SELL

    alert = PriceAlert(
        id=str(uuid.uuid4()),
        asset_id=asset.id,
        rule=alert_in.rule,
        threshold=alert_in.threshold,
        intent=intent,
        trail_percent=alert_in.trail_percent,
        trail_amount=alert_in.trail_amount,
        high_water_mark=high_water_mark,
        note=alert_in.note,
        is_active=True,
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return _serialize(alert)


@router.put("/{alert_id}", response_model=PriceAlertOut)
async def update_alert(
    alert_id: str,
    alert_in: PriceAlertUpdate,
    db: Session = Depends(get_db),
):
    """Edit rule/threshold/trail/intent/note, or reactivate an alert.

    Setting isActive=true clears triggered/acknowledged/notified state so the
    alert can fire again (one-shot re-arm); a reactivated trailing stop also
    restarts its high-water mark from the current price."""
    alert = db.query(PriceAlert).filter(PriceAlert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    update_data = alert_in.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    if update_data.get("threshold") is not None and update_data["threshold"] <= 0:
        raise HTTPException(status_code=400, detail="Threshold must be positive")
    for trail_field in ("trail_percent", "trail_amount"):
        if update_data.get(trail_field) is not None and update_data[trail_field] <= 0:
            raise HTTPException(status_code=400, detail="Trail must be positive")

    reactivating = update_data.get("is_active") is True

    for field, value in update_data.items():
        setattr(alert, field, value)

    # Validate the resulting state so edits can't leave an alert unrunnable.
    if alert.rule == AlertRule.TRAILING_STOP:
        if (alert.trail_percent is None) == (alert.trail_amount is None):
            raise HTTPException(
                status_code=400,
                detail="Trailing stop requires exactly one of trailPercent or trailAmount",
            )
        if alert.high_water_mark is None or reactivating:
            alert.high_water_mark = await _fetch_current_price(alert.asset, db)
    elif alert.threshold is None:
        raise HTTPException(status_code=400, detail="Threshold is required for price rules")

    if reactivating:
        alert.triggered_at = None
        alert.triggered_price = None
        alert.acknowledged_at = None
        alert.notified_at = None

    db.commit()
    db.refresh(alert)
    return _serialize(alert)


@router.delete("/{alert_id}", status_code=204)
async def delete_alert(alert_id: str, db: Session = Depends(get_db)):
    alert = db.query(PriceAlert).filter(PriceAlert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    db.delete(alert)
    db.commit()
    return None


@router.post("/{alert_id}/acknowledge", response_model=PriceAlertOut)
async def acknowledge_alert(alert_id: str, db: Session = Depends(get_db)):
    alert = db.query(PriceAlert).filter(PriceAlert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    if alert.acknowledged_at is None:
        alert.acknowledged_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(alert)

    return _serialize(alert)
