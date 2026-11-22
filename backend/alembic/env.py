"""Alembic environment: online only, serialised by MIGRATION_LOCK_KEY.

Callers may pass `config.attributes["engine"]` (tests, app.migrate); otherwise
the URL comes from settings.database_url. The session-level advisory lock is
taken here unless `config.attributes["lock_held"]` says the caller holds it, so
a bare `alembic upgrade head` is serialised too, without self-deadlock.
"""
import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401  (registers every table on Base.metadata)
from app.config import settings
from app.database import Base
from app.locks import MIGRATION_LOCK_KEY

config = context.config

# Configure logging from alembic.ini only when nobody else has (CLI or
# `python -m app.migrate`); in-process callers such as pytest keep theirs.
if config.config_file_name is not None and not logging.getLogger().handlers:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    raise RuntimeError("offline (--sql) migrations are not supported")


def run_migrations_online() -> None:
    engine = config.attributes.get("engine")
    owns_engine = engine is None
    if owns_engine:
        engine = create_engine(settings.database_url, poolclass=NullPool, hide_parameters=True)
    take_lock = not config.attributes.get("lock_held", False)
    try:
        with engine.connect() as connection:
            if take_lock:
                connection.exec_driver_sql(f"SELECT pg_advisory_lock({MIGRATION_LOCK_KEY})")
                connection.commit()  # the session-level lock survives the commit
            try:
                context.configure(
                    connection=connection,
                    target_metadata=target_metadata,
                    compare_type=True,
                )
                with context.begin_transaction():
                    context.run_migrations()
            finally:
                if take_lock:
                    # Release explicitly: a caller-supplied engine pools the connection.
                    connection.rollback()
                    connection.exec_driver_sql(f"SELECT pg_advisory_unlock({MIGRATION_LOCK_KEY})")
                    connection.commit()
    finally:
        if owns_engine:
            engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
