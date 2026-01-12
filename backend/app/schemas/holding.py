from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from .asset import Currency, Asset


class HoldingLotBase(BaseModel):
    asset_id: str = Field(alias="assetId")
    quantity: float
    avg_cost: float = Field(alias="avgCost")
    cost_currency: Currency = Field(alias="costCurrency")
    account_name: str = Field(alias="accountName")
    tags: Optional[List[str]] = None
    purchase_date: datetime = Field(alias="purchaseDate")

    class Config:
        populate_by_name = True
        use_enum_values = True


class HoldingLotCreate(HoldingLotBase):
    pass


class HoldingLot(HoldingLotBase):
    id: str
    created_at: datetime = Field(alias="createdAt")

    class Config:
        from_attributes = True
        populate_by_name = True
        use_enum_values = True


class CashBalanceBase(BaseModel):
    currency: Currency
    amount: float
    account_name: str = Field(alias="accountName")

    class Config:
        populate_by_name = True
        use_enum_values = True


class CashBalanceCreate(CashBalanceBase):
    pass


class CashBalance(CashBalanceBase):
    id: str

    class Config:
        from_attributes = True
        populate_by_name = True
        use_enum_values = True


class HoldingWithAsset(HoldingLot):
    """Holding with populated asset and current market data."""
    asset: Asset
    current_price: Optional[float] = Field(default=None, alias="currentPrice")
    price_change: Optional[float] = Field(default=None, alias="priceChange")
    price_change_percent: Optional[float] = Field(default=None, alias="priceChangePercent")
    market_value: Optional[float] = Field(default=None, alias="marketValue")
    unrealized_pnl: Optional[float] = Field(default=None, alias="unrealizedPnL")
    unrealized_pnl_percent: Optional[float] = Field(default=None, alias="unrealizedPnLPercent")
    weight: Optional[float] = None

    class Config:
        from_attributes = True
        populate_by_name = True
        use_enum_values = True


class GroupedHolding(BaseModel):
    """Holdings grouped by asset for display."""
    asset_id: str = Field(alias="assetId")
    asset: Asset
    lots: List[HoldingWithAsset]
    total_quantity: float = Field(alias="totalQuantity")
    avg_cost: float = Field(alias="avgCost")
    current_price: Optional[float] = Field(default=None, alias="currentPrice")
    price_change: Optional[float] = Field(default=None, alias="priceChange")
    price_change_percent: Optional[float] = Field(default=None, alias="priceChangePercent")
    market_value: Optional[float] = Field(default=None, alias="marketValue")
    unrealized_pnl: Optional[float] = Field(default=None, alias="unrealizedPnL")
    unrealized_pnl_percent: Optional[float] = Field(default=None, alias="unrealizedPnLPercent")
    weight: Optional[float] = None

    class Config:
        populate_by_name = True
        use_enum_values = True
