from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from enum import Enum


class DataSource(str, Enum):
    DEMO = "demo"
    ALPHAVANTAGE = "alphavantage"
    TWELVEDATA = "twelvedata"
    FINNHUB = "finnhub"
    POLYGON = "polygon"


class MarketRegion(str, Enum):
    US = "US"
    EU = "EU"
    ASIA = "ASIA"


class MarketCard(BaseModel):
    id: str
    symbol: str
    name: str
    data_source: DataSource = Field(alias="dataSource")
    order: int
    region: Optional[MarketRegion] = None

    class Config:
        populate_by_name = True
        use_enum_values = True


class MarketCardData(MarketCard):
    price: Optional[float] = None
    change: Optional[float] = None
    change_percent: Optional[float] = Field(default=None, alias="changePercent")
    currency: Optional[str] = None
    last_updated: Optional[datetime] = Field(default=None, alias="lastUpdated")
    error: Optional[str] = None

    class Config:
        populate_by_name = True
        use_enum_values = True
