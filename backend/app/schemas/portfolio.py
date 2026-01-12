from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from .asset import Currency, Asset


class PortfolioSummary(BaseModel):
    total_nav: float = Field(alias="totalNav")
    base_currency: Currency = Field(alias="baseCurrency")
    daily_pnl: float = Field(alias="dailyPnL")
    daily_pnl_percent: float = Field(alias="dailyPnLPercent")
    weekly_pnl_percent: Optional[float] = Field(default=None, alias="weeklyPnLPercent")
    monthly_pnl_percent: Optional[float] = Field(default=None, alias="monthlyPnLPercent")
    total_cost: float = Field(alias="totalCost")
    total_unrealized_pnl: float = Field(alias="totalUnrealizedPnL")
    total_unrealized_pnl_percent: float = Field(alias="totalUnrealizedPnLPercent")
    last_updated: datetime = Field(alias="lastUpdated")

    class Config:
        populate_by_name = True
        use_enum_values = True


class MarketMover(BaseModel):
    asset: Asset
    price: float
    change: float
    change_percent: float = Field(alias="changePercent")

    class Config:
        populate_by_name = True
        use_enum_values = True
