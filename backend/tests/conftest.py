"""Shared pytest fixtures for backend tests.

Uses an in-memory SQLite DB. SQLite has no native ARRAY type, so we teach
it to compile `HoldingLot.tags` (a Postgres ARRAY column in production) as
a JSON column purely for test purposes - no production model changes.
"""
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, ARRAY
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Ensure `app` package is importable when running pytest from the backend/ dir.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@compiles(ARRAY, "sqlite")
def _compile_array_as_json_for_sqlite(element, compiler, **kw):
    return "JSON"


from app.database import Base  # noqa: E402
# Import all models so they're registered on Base.metadata before create_all.
from app.models import asset, holding, price, watchlist, news, alert  # noqa: E402,F401


@pytest.fixture()
def db_session():
    """A fresh in-memory SQLite session per test.

    StaticPool (one shared connection) instead of the default per-thread
    pool: tests that drive requests through TestClient run ASGI calls on a
    worker thread, and a per-thread pool would hand that thread a brand-new
    (tableless) :memory: database.
    """
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


@pytest.fixture(autouse=True)
def no_real_notifications(monkeypatch):
    """Keep the scheduler's notification pass off the network in every test.

    evaluate_price_alerts() constructs NotificationService directly; without
    this guard, a developer with real Telegram credentials in backend/.env
    would send actual messages just by running the suite. Tests that need to
    observe notification behavior override the same attribute themselves.
    """
    from app.tasks import scheduler as scheduler_module

    class _DisabledNotificationService:
        enabled = False

        async def send(self, text: str) -> bool:
            return False

    monkeypatch.setattr(
        scheduler_module, "NotificationService", _DisabledNotificationService
    )


@pytest.fixture()
def client(db_session):
    """TestClient over the alerts router, bound to the shared test session."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.routers import alerts as alerts_router_module

    app = FastAPI()
    app.include_router(alerts_router_module.router, prefix="/api/alerts")
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


@pytest.fixture()
def eval_db(db_session, monkeypatch):
    """Point the scheduler's SessionLocal() calls at the shared test session
    so evaluate_price_alerts() operates on the same in-memory DB.

    evaluate_price_alerts() calls db.close() in its own finally block (as it
    does in production, where it owns the session end-to-end) — that expunges
    objects from the identity map, so tests re-query by id afterwards instead
    of calling session.refresh() on the original instances.
    """
    from app.tasks import scheduler as scheduler_module

    monkeypatch.setattr(scheduler_module, "SessionLocal", lambda: db_session)
    return db_session
