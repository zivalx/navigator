from sqlalchemy import Column, String, Float, ForeignKey, DateTime, Text, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ..database import Base


class NewsItem(Base):
    __tablename__ = "news_items"

    id = Column(String, primary_key=True)
    asset_id = Column(String, ForeignKey("assets.id", ondelete="SET NULL"), nullable=True)
    title = Column(String, nullable=False)
    summary = Column(Text, nullable=False)
    url = Column(String, nullable=False, unique=True)
    publisher = Column(String, nullable=False)
    published_at = Column(DateTime(timezone=True), nullable=False, index=True)
    sentiment_score = Column(Float, nullable=False)  # -1 to 1
    relevance_score = Column(Float, nullable=False)  # 0 to 1

    # Relationships
    asset = relationship("Asset", back_populates="news")

    # Index for efficient news queries
    __table_args__ = (
        Index("ix_news_published_at", "published_at"),
        Index("ix_news_asset_published", "asset_id", "published_at"),
    )
