from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timedelta

from ..database import get_db
from ..schemas.news import NewsItem
from ..models.news import NewsItem as NewsModel
from ..services.news import NewsService

router = APIRouter()


@router.get("/", response_model=List[NewsItem])
async def get_news(
    asset_id: Optional[str] = None,
    limit: int = Query(default=20, le=100),
    db: Session = Depends(get_db)
):
    """Get news items, optionally filtered by asset."""
    news_service = NewsService(db)

    if asset_id:
        return await news_service.get_news_for_asset(asset_id, limit)
    else:
        return await news_service.get_latest_news(limit)


@router.get("/sync")
async def sync_news(db: Session = Depends(get_db)):
    """Manually trigger news sync from external sources."""
    news_service = NewsService(db)
    count = await news_service.sync_news()
    return {"synced": count}


@router.get("/symbols/{symbol}", response_model=List[NewsItem])
async def get_news_by_symbol(
    symbol: str,
    limit: int = Query(default=10, le=50),
    db: Session = Depends(get_db)
):
    """Get news for a specific symbol."""
    news_service = NewsService(db)
    return await news_service.get_news_by_symbol(symbol, limit)
