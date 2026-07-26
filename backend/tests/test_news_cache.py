"""Tests for news list caching (routers/news.py).

Verifies that news_cache_ttl is actually honored (it used to be dead
config), that a cache hit skips the DB path, and that a successful /sync
bumps the generation counter so stale lists are invalidated immediately.

Follows the suite's pattern: bare FastAPI app + in-memory cache monkeypatch.
"""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.models.asset import Asset, AssetType, Currency, MarketRegion
from app.models.news import NewsItem as NewsModel
from app.routers import news as news_router
from app.services.news import NewsService


@pytest.fixture()
def db_session():
    """StaticPool (single shared connection) so the TestClient's worker
    thread sees the same in-memory SQLite DB — same pattern as test_alerts."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def fake_cache(monkeypatch):
    """In-memory stand-in for the Redis cache used by the news router.
    Records (value, ttl) per key so tests can assert on TTLs too."""
    store = {}

    async def _get(key):
        entry = store.get(key)
        return entry[0] if entry else None

    async def _set(key, value, ttl=60):
        store[key] = (value, ttl)

    monkeypatch.setattr("app.routers.news.cache.get", _get)
    monkeypatch.setattr("app.routers.news.cache.set", _set)
    return store


@pytest.fixture()
def client(db_session):
    app = FastAPI()
    app.include_router(news_router.router, prefix="/api/news")
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def _seed_news(db_session, title="Fed holds rates"):
    asset = Asset(
        id=str(uuid.uuid4()),
        symbol="AAPL",
        name="Apple Inc.",
        currency=Currency.USD,
        asset_type=AssetType.STOCK,
        market_region=MarketRegion.US,
    )
    db_session.add(asset)
    item = NewsModel(
        id=str(uuid.uuid4()),
        asset_id=asset.id,
        title=title,
        summary="Summary text",
        url=f"https://example.com/{uuid.uuid4()}",
        publisher="Example Wire",
        published_at=datetime.now(timezone.utc),
        sentiment_score=0.2,
        relevance_score=0.9,
    )
    db_session.add(item)
    db_session.commit()
    return item


def test_news_list_is_cached_with_configured_ttl(client, db_session, fake_cache):
    _seed_news(db_session)

    resp = client.get("/api/news/", params={"limit": 20})
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    # One entry written, keyed by generation 0, with the configured TTL.
    assert "news:0:all:20" in fake_cache
    _, ttl = fake_cache["news:0:all:20"]
    assert ttl == settings.news_cache_ttl


def test_second_call_served_from_cache_not_db(client, db_session, fake_cache, monkeypatch):
    _seed_news(db_session)
    first = client.get("/api/news/", params={"limit": 20}).json()

    # Any DB-path call after the cache is warm is a failure.
    async def _boom(self, limit=20):
        raise AssertionError("DB path used despite warm cache")

    monkeypatch.setattr(NewsService, "get_latest_news", _boom)

    second = client.get("/api/news/", params={"limit": 20})
    assert second.status_code == 200
    assert second.json() == first


def test_sync_bumps_generation_and_invalidates(client, db_session, fake_cache, monkeypatch):
    _seed_news(db_session, title="old headline")
    client.get("/api/news/", params={"limit": 20})
    assert "news:0:all:20" in fake_cache

    async def _fake_sync(self):
        _seed_news(db_session, title="fresh headline")
        return 1

    monkeypatch.setattr(NewsService, "sync_news", _fake_sync)
    resp = client.get("/api/news/sync")
    assert resp.json() == {"synced": 1}

    # Generation bumped (stored without expiry) -> next list uses a new key
    # and sees the fresh article without waiting out the TTL.
    gen, ttl = fake_cache["news:gen"]
    assert gen == 1
    assert ttl is None

    titles = {item["title"] for item in client.get("/api/news/", params={"limit": 20}).json()}
    assert "fresh headline" in titles
    assert "news:1:all:20" in fake_cache


def test_failed_sync_does_not_invalidate(client, db_session, fake_cache, monkeypatch):
    async def _fake_sync(self):
        return 0  # keyless / failed sync path returns 0

    monkeypatch.setattr(NewsService, "sync_news", _fake_sync)
    client.get("/api/news/sync")
    assert "news:gen" not in fake_cache


def test_symbol_news_cached_per_symbol(client, db_session, fake_cache):
    _seed_news(db_session)
    resp = client.get("/api/news/symbols/aapl", params={"limit": 10})
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert "news:0:sym:AAPL:10" in fake_cache
