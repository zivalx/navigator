from sqlalchemy import Boolean, Column, DateTime, Enum, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ..database import Base
import enum


class AlertRule(str, enum.Enum):
    PRICE_BELOW = "price_below"
    PRICE_ABOVE = "price_above"
    TRAILING_STOP = "trailing_stop"


class AlertIntent(str, enum.Enum):
    BUY = "buy"
    SELL = "sell"


class PriceAlert(Base):
    __tablename__ = "price_alerts"

    id = Column(String, primary_key=True)
    asset_id = Column(String, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True)
    rule = Column(Enum(AlertRule), nullable=False)
    # Required for price_below/price_above; null for trailing_stop (the stop
    # level is derived from high_water_mark + trail, never stored).
    threshold = Column(Float, nullable=True)
    intent = Column(Enum(AlertIntent), nullable=True)
    # Trailing-stop fields: exactly one of trail_percent/trail_amount is set.
    trail_percent = Column(Float, nullable=True)
    trail_amount = Column(Float, nullable=True)
    # Highest price observed since the alert was created/reactivated;
    # ratchets up, never down.
    high_water_mark = Column(Float, nullable=True)
    note = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    triggered_at = Column(DateTime(timezone=True), nullable=True)
    triggered_price = Column(Float, nullable=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    # notified_at means DELIVERED. notification_skipped_at means the trigger
    # fired while no channel was configured — kept distinct so a transient
    # config gap is auditable instead of masquerading as a successful send.
    notified_at = Column(DateTime(timezone=True), nullable=True)
    notification_skipped_at = Column(DateTime(timezone=True), nullable=True)

    # One-sided relationship: Asset does not declare a back_populates for
    # this (app/models/asset.py is intentionally left untouched).
    asset = relationship("Asset")

    __table_args__ = (
        Index("ix_price_alerts_asset_active", "asset_id", "is_active"),
    )

    @property
    def stop_price(self) -> float | None:
        """Derived trailing-stop level, or None for non-TSL alerts."""
        if self.rule != AlertRule.TRAILING_STOP or self.high_water_mark is None:
            return None
        if self.trail_percent is not None:
            return self.high_water_mark * (1 - self.trail_percent / 100)
        if self.trail_amount is not None:
            return self.high_water_mark - self.trail_amount
        return None
