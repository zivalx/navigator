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
    DBs are created fresh from the models). Probes information_schema first so
    fully-migrated databases skip the DDL entirely (no locks on every boot).
    """
    if engine.dialect.name != "postgresql":
        return

    with engine.connect() as conn:
        existing = {
            row[0]
            for row in conn.exec_driver_sql(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'price_alerts'"
            )
        }
    new_columns = {
        "intent": "alertintent",
        "trail_percent": "double precision",
        "trail_amount": "double precision",
        "high_water_mark": "double precision",
        "notified_at": "timestamptz",
        "notification_skipped_at": "timestamptz",
    }
    missing = {name: ddl for name, ddl in new_columns.items() if name not in existing}
    if not missing:
        return

    statements = [
        # SQLAlchemy Enum columns store member *names*; extend the existing type.
        "ALTER TYPE alertrule ADD VALUE IF NOT EXISTS 'TRAILING_STOP'",
        (
            "DO $$ BEGIN CREATE TYPE alertintent AS ENUM ('BUY', 'SELL'); "
            "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
        ),
        *[
            f"ALTER TABLE price_alerts ADD COLUMN IF NOT EXISTS {name} {ddl}"
            for name, ddl in missing.items()
        ],
        "ALTER TABLE price_alerts ALTER COLUMN threshold DROP NOT NULL",
    ]
    if "notified_at" in missing:
        # Alerts that triggered before this feature existed were already seen
        # in the UI; stamp them delivered so the first notification pass
        # doesn't blast the whole history to Telegram.
        statements.append(
            "UPDATE price_alerts SET notified_at = triggered_at "
            "WHERE triggered_at IS NOT NULL AND notified_at IS NULL"
        )
    # AUTOCOMMIT so ALTER TYPE ADD VALUE takes effect before later statements
    # reference the new value/type.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for statement in statements:
            conn.exec_driver_sql(statement)
