from sqlalchemy import Column, String, Enum, JSON
from sqlalchemy.orm import relationship
from ..database import Base
import enum


class AssetType(str, enum.Enum):
    STOCK = "stock"
    ETF = "etf"
    CRYPTO = "crypto"
    FUND = "fund"
    OTHER = "other"


class MarketRegion(str, enum.Enum):
    US = "US"
    EU = "EU"
    ASIA = "ASIA"


class Currency(str, enum.Enum):
    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"
    JPY = "JPY"
    CHF = "CHF"


class Asset(Base):
    __tablename__ = "assets"

    id = Column(String, primary_key=True)
    symbol = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    exchange = Column(String, nullable=True)
    currency = Column(Enum(Currency), nullable=False)
    asset_type = Column(Enum(AssetType), nullable=False)
    market_region = Column(Enum(MarketRegion), nullable=False)
    provider_ids = Column(JSON, nullable=True)  # {"polygon": "AAPL", "finnhub": "AAPL"}

    # Relationships
    holdings = relationship("HoldingLot", back_populates="asset", cascade="all, delete-orphan")
    watchlist_items = relationship("WatchlistItem", back_populates="asset", cascade="all, delete-orphan")
    prices = relationship("PriceSnapshot", back_populates="asset", cascade="all, delete-orphan")
    news = relationship("NewsItem", back_populates="asset", cascade="all, delete-orphan")
