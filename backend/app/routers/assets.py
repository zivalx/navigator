from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List
import logging
import uuid

from ..database import get_db
from ..schemas import asset as schemas
from ..models import asset as models

from ..services.asset_identity import (
    ListingUnavailable,
    SymbolNotFound,
    UnsupportedCurrency,
    derive_listing,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/", response_model=List[schemas.Asset])
async def get_assets(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=0, le=1000),
    db: Session = Depends(get_db)
):
    """Get all assets."""
    assets = db.query(models.Asset).offset(skip).limit(limit).all()
    return assets


@router.get("/{asset_id}", response_model=schemas.Asset)
async def get_asset(asset_id: str, db: Session = Depends(get_db)):
    """Get a specific asset by ID."""
    asset = db.query(models.Asset).filter(models.Asset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset


@router.get("/symbol/{symbol}", response_model=schemas.Asset)
async def get_asset_by_symbol(symbol: str, db: Session = Depends(get_db)):
    """Get an asset by symbol."""
    asset = db.query(models.Asset).filter(
        models.Asset.symbol == symbol.upper()
    ).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset


@router.post("/", response_model=schemas.Asset, status_code=201)
async def create_asset(
    asset_in: schemas.AssetCreate,
    db: Session = Depends(get_db)
):
    """Create a new asset."""
    if not asset_in.symbol.strip():
        raise HTTPException(status_code=400, detail="Symbol cannot be empty")
    if not asset_in.name.strip():
        raise HTTPException(status_code=400, detail="Name cannot be empty")

    # Check if asset with symbol already exists
    existing = db.query(models.Asset).filter(
        models.Asset.symbol == asset_in.symbol.upper()
    ).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Asset with symbol {asset_in.symbol} already exists"
        )

    symbol = asset_in.symbol.upper()
    asset_type = schemas.AssetType(asset_in.asset_type)
    exchange = asset_in.exchange
    currency = asset_in.currency
    region = asset_in.market_region

    if asset_type in (schemas.AssetType.STOCK, schemas.AssetType.ETF, schemas.AssetType.FUND):
        # Identity comes from the venue, never from the client.
        try:
            listing = await derive_listing(symbol)
        except ListingUnavailable:
            raise HTTPException(status_code=503, detail="couldn't verify symbol, try again")
        except UnsupportedCurrency as e:
            raise HTTPException(status_code=422, detail=str(e))
        except SymbolNotFound:
            raise HTTPException(status_code=404, detail="symbol not found")
        currency = listing.currency.value
        exchange = listing.exchange
        region = listing.market_region.value if listing.market_region else None
        if region is None:
            logger.warning("unknown venue %s for %s", listing.exchange, symbol)
            region = asset_in.market_region or schemas.MarketRegion.US.value
    elif asset_type == schemas.AssetType.CRYPTO:
        currency = currency or schemas.Currency.USD.value
        region = region or schemas.MarketRegion.US.value
    else:
        if not currency:
            raise HTTPException(status_code=422, detail="currency is required for other assets")
        region = region or schemas.MarketRegion.US.value

    asset = models.Asset(
        id=str(uuid.uuid4()),
        symbol=symbol,
        name=asset_in.name,
        exchange=exchange,
        currency=currency,
        asset_type=asset_in.asset_type,
        market_region=region,
        provider_ids=asset_in.provider_ids,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset


@router.put("/{asset_id}", response_model=schemas.Asset)
async def update_asset(
    asset_id: str,
    asset_in: schemas.AssetUpdate,
    db: Session = Depends(get_db)
):
    """Update an asset."""
    asset = db.query(models.Asset).filter(models.Asset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    update_data = asset_in.model_dump(exclude_unset=True)

    # Validate symbol uniqueness if changing symbol
    if "symbol" in update_data:
        new_symbol = update_data["symbol"].upper() if update_data["symbol"] else ""
        if not new_symbol.strip():
            raise HTTPException(status_code=400, detail="Symbol cannot be empty")
        existing = db.query(models.Asset).filter(
            models.Asset.symbol == new_symbol,
            models.Asset.id != asset_id
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"Asset with symbol {new_symbol} already exists")
        update_data["symbol"] = new_symbol

    if "name" in update_data and not update_data["name"].strip():
        raise HTTPException(status_code=400, detail="Name cannot be empty")

    for field, value in update_data.items():
        setattr(asset, field, value)

    db.commit()
    db.refresh(asset)
    return asset


@router.delete("/{asset_id}", status_code=204)
async def delete_asset(asset_id: str, db: Session = Depends(get_db)):
    """Delete an asset."""
    asset = db.query(models.Asset).filter(models.Asset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    db.delete(asset)
    db.commit()
    return None
