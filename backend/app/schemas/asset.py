from pydantic import BaseModel, Field
from typing import Optional, Dict
from enum import Enum


class AssetType(str, Enum):
    STOCK = "stock"
    ETF = "etf"
    CRYPTO = "crypto"
    FUND = "fund"
    OTHER = "other"


class MarketRegion(str, Enum):
    US = "US"
    EU = "EU"
    ASIA = "ASIA"


class Currency(str, Enum):
    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"
    JPY = "JPY"
    CHF = "CHF"


class AssetBase(BaseModel):
    symbol: str
    name: str
    exchange: Optional[str] = None
    currency: Currency
    asset_type: AssetType = Field(alias="assetType")
    market_region: MarketRegion = Field(alias="marketRegion")
    provider_ids: Optional[Dict[str, str]] = Field(default=None, alias="providerIds")

    class Config:
        populate_by_name = True
        use_enum_values = True


class AssetCreate(AssetBase):
    pass


class AssetUpdate(BaseModel):
    symbol: Optional[str] = None
    name: Optional[str] = None
    exchange: Optional[str] = None
    currency: Optional[Currency] = None
    asset_type: Optional[AssetType] = Field(default=None, alias="assetType")
    market_region: Optional[MarketRegion] = Field(default=None, alias="marketRegion")
    provider_ids: Optional[Dict[str, str]] = Field(default=None, alias="providerIds")

    class Config:
        populate_by_name = True
        use_enum_values = True


class Asset(AssetBase):
    id: str

    class Config:
        from_attributes = True
        populate_by_name = True
        use_enum_values = True
