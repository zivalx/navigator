from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
import uuid

from ..database import get_db
from ..schemas import asset as schemas
from ..models import asset as models

router = APIRouter()


@router.get("/", response_model=List[schemas.Asset])
async def get_assets(
    skip: int = 0,
    limit: int = 100,
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
    # Check if asset with symbol already exists
    existing = db.query(models.Asset).filter(
        models.Asset.symbol == asset_in.symbol.upper()
    ).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Asset with symbol {asset_in.symbol} already exists"
        )

    asset = models.Asset(
        id=str(uuid.uuid4()),
        symbol=asset_in.symbol.upper(),
        name=asset_in.name,
        exchange=asset_in.exchange,
        currency=asset_in.currency,
        asset_type=asset_in.asset_type,
        market_region=asset_in.market_region,
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
