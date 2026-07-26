from typing import List, Optional
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import httpx
import logging
import uuid

from ..models.news import NewsItem
from ..models.asset import Asset
from ..config import settings

logger = logging.getLogger(__name__)


class NewsService:
    """Service for news aggregation and sentiment."""

    def __init__(self, db: Session):
        self.db = db
        self.client = httpx.AsyncClient(timeout=15.0)

    async def get_latest_news(self, limit: int = 20) -> List[NewsItem]:
        """Get latest news items."""
        news = self.db.query(NewsItem).order_by(
            NewsItem.published_at.desc()
        ).limit(limit).all()
        return news

    async def get_news_for_asset(self, asset_id: str, limit: int = 10) -> List[NewsItem]:
        """Get news for specific asset."""
        news = self.db.query(NewsItem).filter(
            NewsItem.asset_id == asset_id
        ).order_by(
            NewsItem.published_at.desc()
        ).limit(limit).all()
        return news

    async def get_news_by_symbol(self, symbol: str, limit: int = 10) -> List[NewsItem]:
        """Get news for a symbol."""
        asset = self.db.query(Asset).filter(Asset.symbol == symbol.upper()).first()
        if not asset:
            return []

        return await self.get_news_for_asset(asset.id, limit)

    async def sync_news(self) -> int:
        """
        Sync news from external sources (Marketaux).

        Returns number of new articles added.
        """
        if not settings.marketaux_api_key:
            logger.warning("Marketaux API key not configured, skipping news sync")
            return 0

        try:
            # Fetch from Marketaux
            url = "https://api.marketaux.com/v1/news/all"
            params = {
                "api_token": settings.marketaux_api_key,
                "language": "en",
                "filter_entities": "true",
                "limit": 50,
            }

            response = await self.client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            count = 0
            for article in data.get("data", []):
                # Check if article already exists
                existing = self.db.query(NewsItem).filter(
                    NewsItem.url == article["url"]
                ).first()

                if existing:
                    continue

                # Extract sentiment and relevance
                sentiment_score = article.get("sentiment_score", 0) or 0
                # Normalize sentiment from -10 to 10 scale to -1 to 1
                sentiment_score = sentiment_score / 10.0

                # Get tickers/entities
                entities = article.get("entities", [])
                tickers = [e["symbol"] for e in entities if e.get("symbol")]

                # Create news item
                news_item = NewsItem(
                    id=str(uuid.uuid4()),
                    asset_id=None,  # We'll link to asset if we can
                    title=article["title"],
                    summary=article.get("snippet", article.get("description", ""))[:500],
                    url=article["url"],
                    publisher=article.get("source", "Unknown"),
                    published_at=datetime.fromisoformat(article["published_at"].replace("Z", "+00:00")),
                    sentiment_score=sentiment_score,
                    relevance_score=0.5,  # Default relevance
                )

                # Try to link to first ticker if available
                if tickers:
                    asset = self.db.query(Asset).filter(
                        Asset.symbol == tickers[0].upper()
                    ).first()
                    if asset:
                        news_item.asset_id = asset.id
                        news_item.relevance_score = 0.9

                self.db.add(news_item)
                count += 1

            self.db.commit()
            logger.info("Synced %d news articles", count)
            return count

        except Exception as e:
            logger.error("Error syncing news: %s", e)
            self.db.rollback()
            return 0

    async def cleanup_old_news(self, days: int = 30):
        """Delete news older than specified days."""
        cutoff = datetime.now() - timedelta(days=days)
        deleted = self.db.query(NewsItem).filter(
            NewsItem.published_at < cutoff
        ).delete()
        self.db.commit()
        return deleted

    async def close(self):
        """Close HTTP client."""
        await self.client.aclose()
