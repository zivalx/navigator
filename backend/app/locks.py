"""Postgres advisory-lock keys owned by Navigator (one place, never reused).

7400419 / 7400420 belong to trender; don't take them here.
"""
import logging
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Connection, Engine

logger = logging.getLogger(__name__)

MIGRATION_LOCK_KEY = 7400417  # python -m app.migrate and alembic/env.py
SCHEDULER_LOCK_KEY = 7400418  # scheduler leader election


@contextmanager
def advisory_lock(engine: Engine, key: int) -> Iterator[Connection]:
    """Hold session-level advisory lock `key` on a dedicated AUTOCOMMIT
    connection (yielded) for the duration of the block. Blocks until free."""
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql(f"SELECT pg_advisory_lock({int(key)})")
        try:
            yield conn
        finally:
            try:
                conn.exec_driver_sql(f"SELECT pg_advisory_unlock({int(key)})")
            except Exception:  # noqa: BLE001  (don't mask the block's own error)
                # Closing the session releases the lock; never pool it still held.
                logger.warning("advisory unlock %s failed; discarding the connection", key)
                conn.invalidate()
