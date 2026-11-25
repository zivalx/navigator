"""Opt-in Postgres test helpers (run only when TEST_POSTGRES_URL is set).

Each test gets its own scratch schema; the yielded engine pins
search_path to it, so unqualified DDL, Alembic's alembic_version table and
the loaded fixtures all land there. The schema is dropped CASCADE on exit.
"""
import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.pool import NullPool

FIXTURES = Path(__file__).resolve().parent / "fixtures"

requires_pg = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"), reason="TEST_POSTGRES_URL not set"
)


@contextmanager
def scratch_schema() -> Iterator[Engine]:
    url = os.environ["TEST_POSTGRES_URL"]
    schema = f"t_{uuid.uuid4().hex[:8]}"
    admin = create_engine(url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    with admin.connect() as c:
        c.exec_driver_sql(f"CREATE SCHEMA {schema}")
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as c:
            c.exec_driver_sql(f"DROP SCHEMA {schema} CASCADE")
        admin.dispose()


def load_sql(engine: Engine, path: Path | str) -> None:
    """Run a plain SQL script (no psql meta-commands) in one transaction."""
    with engine.begin() as c:
        c.exec_driver_sql(Path(path).read_text())


def enum_labels(conn: Connection, type_name: str) -> list[str]:
    """Labels of enum `type_name` in current_schema(), in declaration order."""
    return list(conn.execute(
        text(
            "SELECT e.enumlabel FROM pg_enum e "
            "JOIN pg_type t ON t.oid = e.enumtypid "
            "JOIN pg_namespace n ON n.oid = t.typnamespace "
            "WHERE t.typname = :name AND n.nspname = current_schema() "
            "ORDER BY e.enumsortorder"
        ),
        {"name": type_name},
    ).scalars())
