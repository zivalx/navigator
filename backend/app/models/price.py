from sqlalchemy import Column, String, Float, ForeignKey, DateTime, Enum, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ..database import Base
from .asset import Currency


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"

    id = Column(String, primary_key=True)
    asset_id = Column(String, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    price = Column(Float, nullable=False)
    currency = Column(Enum(Currency), nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    source = Column(String, nullable=False)  # "polygon", "finnhub", etc.

    # Relationships
    asset = relationship("Asset", back_populates="prices")

    # Indexes for efficient time-series queries
    __table_args__ = (
        Index("ix_price_asset_timestamp", "asset_id", "timestamp"),
    )


class FxRate(Base):
    __tablename__ = "fx_rates"

    id = Column(String, primary_key=True)
    base_currency = Column(Enum(Currency), nullable=False)
    quote_currency = Column(Enum(Currency), nullable=False)
    rate = Column(Float, nullable=False)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())

    # Index for efficient FX lookups
    __table_args__ = (
        Index("ix_fx_currencies", "base_currency", "quote_currency"),
    )
