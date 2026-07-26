from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from ..database import get_db
from ..schemas.news import NewsItem
from ..services.news import NewsService
from ..cache import cache
from ..config import settings

router = APIRouter()

# News list responses are cached for settings.news_cache_ttl seconds.
# A generation counter is baked into every cache key; a successful manual
# /sync bumps it, so fresh articles show up immediately instead of waiting
# out the TTL. (The Cache wrapper has no pattern-delete, hence this scheme.)
NEWS_CACHE_GEN_KEY = "news:gen"


async def _cache_gen() -> int:
    return await cache.get(NEWS_CACHE_GEN_KEY) or 0


def _serialize(items) -> List[dict]:
    """ORM rows -> alias-keyed dicts (JSON-cacheable, valid for response_model)."""
    return [NewsItem.model_validate(item).model_dump(by_alias=True) for item in items]


@router.get("/", response_model=List[NewsItem])
async def get_news(
    asset_id: Optional[str] = None,
    limit: int = Query(default=20, le=100),
    db: Session = Depends(get_db)
):
    """Get news items, optionally filtered by asset."""
    gen = await _cache_gen()
    cache_key = f"news:{gen}:{asset_id or 'all'}:{limit}"
    cached = await cache.get(cache_key)
    if cached is not None:
        return cached

    news_service = NewsService(db)
    if asset_id:
        items = await news_service.get_news_for_asset(asset_id, limit)
    else:
        items = await news_service.get_latest_news(limit)

    payload = _serialize(items)
    await cache.set(cache_key, payload, ttl=settings.news_cache_ttl)
    return payload


@router.get("/sync")
async def sync_news(db: Session = Depends(get_db)):
    """Manually trigger news sync from external sources."""
    news_service = NewsService(db)
    count = await news_service.sync_news()
    if count:
        # Invalidate all cached news lists by bumping the generation counter.
        gen = await _cache_gen()
        await cache.set(NEWS_CACHE_GEN_KEY, gen + 1, ttl=None)
    return {"synced": count}


@router.get("/symbols/{symbol}", response_model=List[NewsItem])
async def get_news_by_symbol(
    symbol: str,
    limit: int = Query(default=10, le=50),
    db: Session = Depends(get_db)
):
    """Get news for a specific symbol."""
    gen = await _cache_gen()
    cache_key = f"news:{gen}:sym:{symbol.upper()}:{limit}"
    cached = await cache.get(cache_key)
    if cached is not None:
        return cached

    news_service = NewsService(db)
    items = await news_service.get_news_by_symbol(symbol, limit)

    payload = _serialize(items)
    await cache.set(cache_key, payload, ttl=settings.news_cache_ttl)
    return payload
