from sqlalchemy import Column, String, Float, Date, DateTime
from sqlalchemy.sql import func
from ..database import Base


class PortfolioSnapshot(Base):
    """One row per day: the portfolio's EOD NAV and flow-adjusted daily P&L,
    stored canonically in USD (see the daily-pnl-series design spec)."""
    __tablename__ = "portfolio_snapshots"

    id = Column(String, primary_key=True)
    # unique + index: one row per day, fast range scans for the series.
    date = Column(Date, nullable=False, unique=True, index=True)
    nav = Column(Float, nullable=False)
    net_flow = Column(Float, nullable=False, default=0.0)
    daily_pnl = Column(Float, nullable=False, default=0.0)
    daily_pnl_pct = Column(Float, nullable=False, default=0.0)
    base_currency = Column(String, nullable=False, default="USD")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
