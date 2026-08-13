from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from .config import settings

# Create engine
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    echo=settings.app_env == "development",
)

# Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for models
Base = declarative_base()


def get_db():
    """Dependency for database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Initialize database tables."""
    Base.metadata.create_all(bind=engine)
    _migrate_price_alerts()


def _migrate_price_alerts():
    """Additive migration for trailing-stop alert columns.

    create_all() only creates missing tables, so pre-existing price_alerts
    tables need the new columns added explicitly. Postgres-only (SQLite test
    DBs are created fresh from the models). Idempotent — safe on every start.
    """
    if engine.dialect.name != "postgresql":
        return

    statements = [
        # SQLAlchemy Enum columns store member *names*; extend the existing type.
        "ALTER TYPE alertrule ADD VALUE IF NOT EXISTS 'TRAILING_STOP'",
        (
            "DO $$ BEGIN CREATE TYPE alertintent AS ENUM ('BUY', 'SELL'); "
            "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
        ),
        "ALTER TABLE price_alerts ADD COLUMN IF NOT EXISTS intent alertintent",
        "ALTER TABLE price_alerts ADD COLUMN IF NOT EXISTS trail_percent double precision",
        "ALTER TABLE price_alerts ADD COLUMN IF NOT EXISTS trail_amount double precision",
        "ALTER TABLE price_alerts ADD COLUMN IF NOT EXISTS high_water_mark double precision",
        "ALTER TABLE price_alerts ADD COLUMN IF NOT EXISTS notified_at timestamptz",
        "ALTER TABLE price_alerts ALTER COLUMN threshold DROP NOT NULL",
    ]
    # AUTOCOMMIT so ALTER TYPE ADD VALUE takes effect before later statements
    # reference the new value/type.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for statement in statements:
            conn.exec_driver_sql(statement)
