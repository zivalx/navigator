from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, sessionmaker
from .config import settings
from .schema_version import db_revision, head_revision

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
    """SQLite (tests, ad hoc dev): create the tables. Postgres: no DDL, only
    check the schema is at the code's Alembic head (python -m app.migrate)."""
    if engine.dialect.name != "postgresql":
        Base.metadata.create_all(bind=engine)
        ensure_cutover_marker()
        return

    with engine.connect() as conn:
        rev = db_revision(conn)
    head = head_revision()
    if rev != head:
        raise RuntimeError(f"DB at {rev}, code expects {head}; run python -m app.migrate")


def ensure_cutover_marker(target_engine: Engine | None = None) -> None:
    """Record when minor-unit normalisation went live (idempotent, race-safe).
    Uses SessionLocal unless `target_engine` is given."""
    from sqlalchemy.exc import IntegrityError
    from .models.maintenance import CUTOVER_MARKER, set_marker

    db = SessionLocal() if target_engine is None else Session(bind=target_engine)
    try:
        set_marker(db, CUTOVER_MARKER)
    except IntegrityError:
        db.rollback()  # the other worker inserted it first
    finally:
        db.close()
