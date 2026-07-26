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

# Ensure `app` package is importable when running pytest from the backend/ dir.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@compiles(ARRAY, "sqlite")
def _compile_array_as_json_for_sqlite(element, compiler, **kw):
    return "JSON"


from app.database import Base  # noqa: E402
# Import all models so they're registered on Base.metadata before create_all.
from app.models import asset, holding, price, watchlist, news  # noqa: E402,F401


@pytest.fixture()
def db_session():
    """A fresh in-memory SQLite session per test."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
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
