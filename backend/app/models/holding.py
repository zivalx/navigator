from sqlalchemy import Column, String, Float, ForeignKey, DateTime, Enum, ARRAY
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ..database import Base
from .asset import Currency


class HoldingLot(Base):
    __tablename__ = "holding_lots"

    id = Column(String, primary_key=True)
    asset_id = Column(String, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    quantity = Column(Float, nullable=False)
    avg_cost = Column(Float, nullable=False)
    cost_currency = Column(Enum(Currency), nullable=False)
    account_name = Column(String, nullable=False)
    tags = Column(ARRAY(String), nullable=True)
    purchase_date = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    asset = relationship("Asset", back_populates="holdings")


class CashBalance(Base):
    __tablename__ = "cash_balances"

    id = Column(String, primary_key=True)
    currency = Column(Enum(Currency), nullable=False)
    amount = Column(Float, nullable=False)
    account_name = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
