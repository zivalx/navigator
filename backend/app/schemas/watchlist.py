from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from .asset import Asset


class WatchlistBase(BaseModel):
    name: str

    class Config:
        populate_by_name = True


class WatchlistCreate(WatchlistBase):
    pass


class WatchlistUpdate(BaseModel):
    name: Optional[str] = None

    class Config:
        populate_by_name = True


class Watchlist(WatchlistBase):
    id: str
    created_at: datetime = Field(alias="createdAt")

    class Config:
        from_attributes = True
        populate_by_name = True


class WatchlistItemBase(BaseModel):
    watchlist_id: str = Field(alias="watchlistId")
    asset_id: str = Field(alias="assetId")
    notes: Optional[str] = None
    target_price: Optional[float] = Field(default=None, alias="targetPrice")

    class Config:
        populate_by_name = True


class WatchlistItemCreate(BaseModel):
    """Create schema — watchlist_id comes from URL, not body."""
    asset_id: str = Field(alias="assetId")
    notes: Optional[str] = None
    target_price: Optional[float] = Field(default=None, alias="targetPrice")

    class Config:
        populate_by_name = True


class WatchlistItem(WatchlistItemBase):
    id: str
    created_at: datetime = Field(alias="createdAt")

    class Config:
        from_attributes = True
        populate_by_name = True


class WatchlistItemUpdate(BaseModel):
    notes: Optional[str] = None
    target_price: Optional[float] = Field(default=None, alias="targetPrice")

    class Config:
        populate_by_name = True


class WatchlistItemWithAsset(WatchlistItem):
    """Watchlist item with populated asset and current price."""
    asset: Asset
    current_price: Optional[float] = Field(default=None, alias="currentPrice")
    price_change: Optional[float] = Field(default=None, alias="priceChange")
    price_change_percent: Optional[float] = Field(default=None, alias="priceChangePercent")
    change_1d: Optional[float] = Field(default=None, alias="change1d")
    change_1m: Optional[float] = Field(default=None, alias="change1m")
    change_6m: Optional[float] = Field(default=None, alias="change6m")

    class Config:
        from_attributes = True
        populate_by_name = True
        use_enum_values = True
