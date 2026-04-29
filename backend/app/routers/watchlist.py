from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
import uuid

from ..database import get_db
from ..schemas import watchlist as schemas
from ..models import watchlist as models
from ..models.asset import Asset

router = APIRouter()


@router.get("/", response_model=List[schemas.Watchlist])
async def get_watchlists(db: Session = Depends(get_db)):
    """Get all watchlists."""
    watchlists = db.query(models.Watchlist).all()
    return watchlists


@router.get("/{watchlist_id}", response_model=schemas.Watchlist)
async def get_watchlist(watchlist_id: str, db: Session = Depends(get_db)):
    """Get a specific watchlist."""
    watchlist = db.query(models.Watchlist).filter(
        models.Watchlist.id == watchlist_id
    ).first()
    if not watchlist:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    return watchlist


@router.post("/", response_model=schemas.Watchlist, status_code=201)
async def create_watchlist(
    watchlist_in: schemas.WatchlistCreate,
    db: Session = Depends(get_db)
):
    """Create a new watchlist."""
    if not watchlist_in.name or not watchlist_in.name.strip():
        raise HTTPException(status_code=400, detail="Watchlist name cannot be empty")

    watchlist = models.Watchlist(
        id=str(uuid.uuid4()),
        name=watchlist_in.name,
    )
    db.add(watchlist)
    db.commit()
    db.refresh(watchlist)
    return watchlist


@router.put("/{watchlist_id}", response_model=schemas.Watchlist)
async def rename_watchlist(
    watchlist_id: str,
    watchlist_in: schemas.WatchlistUpdate,
    db: Session = Depends(get_db)
):
    """Rename a watchlist."""
    watchlist = db.query(models.Watchlist).filter(
        models.Watchlist.id == watchlist_id
    ).first()
    if not watchlist:
        raise HTTPException(status_code=404, detail="Watchlist not found")

    if watchlist_in.name is not None:
        if not watchlist_in.name.strip():
            raise HTTPException(status_code=400, detail="Watchlist name cannot be empty")
        watchlist.name = watchlist_in.name.strip()

    db.commit()
    db.refresh(watchlist)
    return watchlist


@router.delete("/{watchlist_id}", status_code=204)
async def delete_watchlist(watchlist_id: str, db: Session = Depends(get_db)):
    """Delete a watchlist."""
    watchlist = db.query(models.Watchlist).filter(
        models.Watchlist.id == watchlist_id
    ).first()
    if not watchlist:
        raise HTTPException(status_code=404, detail="Watchlist not found")

    db.delete(watchlist)
    db.commit()
    return None


@router.get("/{watchlist_id}/items", response_model=List[schemas.WatchlistItemWithAsset])
async def get_watchlist_items(watchlist_id: str, db: Session = Depends(get_db)):
    """Get all items in a watchlist with current prices (best-effort)."""
    from ..services.market_data import MarketDataService

    # Verify watchlist exists
    watchlist = db.query(models.Watchlist).filter(
        models.Watchlist.id == watchlist_id
    ).first()
    if not watchlist:
        raise HTTPException(status_code=404, detail="Watchlist not found")

    items = db.query(models.WatchlistItem).filter(
        models.WatchlistItem.watchlist_id == watchlist_id
    ).all()

    # Return items immediately, enrich with prices best-effort
    market_service = MarketDataService(db)
    result = []

    for item in items:
        item_dict = {
            "id": item.id,
            "watchlistId": item.watchlist_id,
            "assetId": item.asset_id,
            "notes": item.notes,
            "targetPrice": item.target_price,
            "createdAt": item.created_at,
            "asset": item.asset,
            "currentPrice": None,
            "priceChange": None,
            "priceChangePercent": None,
        }

        # Best-effort price enrichment — never block the response
        try:
            quote = await market_service.get_quote(item.asset.symbol)
            item_dict["currentPrice"] = quote.get("price")
            item_dict["priceChange"] = quote.get("change")
            item_dict["priceChangePercent"] = quote.get("changePercent")
        except Exception:
            pass

        result.append(schemas.WatchlistItemWithAsset(**item_dict))

    return result


@router.post("/{watchlist_id}/items", response_model=schemas.WatchlistItem, status_code=201)
async def add_watchlist_item(
    watchlist_id: str,
    item_in: schemas.WatchlistItemCreate,
    db: Session = Depends(get_db)
):
    """Add an item to a watchlist."""
    # Verify watchlist exists
    watchlist = db.query(models.Watchlist).filter(
        models.Watchlist.id == watchlist_id
    ).first()
    if not watchlist:
        raise HTTPException(status_code=404, detail="Watchlist not found")

    # Verify asset exists
    asset = db.query(Asset).filter(Asset.id == item_in.asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    # Validate target price
    if item_in.target_price is not None and item_in.target_price < 0:
        raise HTTPException(status_code=400, detail="Target price cannot be negative")

    # Check if item already exists
    existing = db.query(models.WatchlistItem).filter(
        models.WatchlistItem.watchlist_id == watchlist_id,
        models.WatchlistItem.asset_id == item_in.asset_id
    ).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail="Asset already in watchlist"
        )

    item = models.WatchlistItem(
        id=str(uuid.uuid4()),
        watchlist_id=watchlist_id,
        asset_id=item_in.asset_id,
        notes=item_in.notes,
        target_price=item_in.target_price,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.put("/items/{item_id}", response_model=schemas.WatchlistItem)
async def update_watchlist_item(
    item_id: str,
    item_in: schemas.WatchlistItemUpdate,
    db: Session = Depends(get_db)
):
    """Update a watchlist item's notes or target price."""
    item = db.query(models.WatchlistItem).filter(
        models.WatchlistItem.id == item_id
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Watchlist item not found")

    update_data = item_in.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    if "target_price" in update_data and update_data["target_price"] is not None and update_data["target_price"] < 0:
        raise HTTPException(status_code=400, detail="Target price cannot be negative")

    for field, value in update_data.items():
        setattr(item, field, value)

    db.commit()
    db.refresh(item)
    return item


@router.delete("/items/{item_id}", status_code=204)
async def delete_watchlist_item(item_id: str, db: Session = Depends(get_db)):
    """Remove an item from a watchlist."""
    item = db.query(models.WatchlistItem).filter(
        models.WatchlistItem.id == item_id
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Watchlist item not found")

    db.delete(item)
    db.commit()
    return None
