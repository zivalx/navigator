from sqlalchemy import Boolean, Column, DateTime, Enum, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ..database import Base
import enum


class AlertRule(str, enum.Enum):
    PRICE_BELOW = "price_below"
    PRICE_ABOVE = "price_above"


class PriceAlert(Base):
    __tablename__ = "price_alerts"

    id = Column(String, primary_key=True)
    asset_id = Column(String, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True)
    rule = Column(Enum(AlertRule), nullable=False)
    threshold = Column(Float, nullable=False)
    note = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    triggered_at = Column(DateTime(timezone=True), nullable=True)
    triggered_price = Column(Float, nullable=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)

    # One-sided relationship: Asset does not declare a back_populates for
    # this (app/models/asset.py is intentionally left untouched).
    asset = relationship("Asset")

    __table_args__ = (
        Index("ix_price_alerts_asset_active", "asset_id", "is_active"),
    )
