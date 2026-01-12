from pydantic import BaseModel, Field
from datetime import datetime
from .asset import Currency


class PriceSnapshot(BaseModel):
    id: str
    asset_id: str = Field(alias="assetId")
    price: float
    currency: Currency
    timestamp: datetime
    source: str

    class Config:
        from_attributes = True
        populate_by_name = True
        use_enum_values = True


class FxRate(BaseModel):
    id: str
    base_currency: Currency = Field(alias="baseCurrency")
    quote_currency: Currency = Field(alias="quoteCurrency")
    rate: float
    timestamp: datetime

    class Config:
        from_attributes = True
        populate_by_name = True
        use_enum_values = True
