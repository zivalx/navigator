"""Dialect-specific INSERT for ON CONFLICT upserts (Postgres in production,
SQLite in tests); both expose on_conflict_do_update / on_conflict_do_nothing."""
from typing import Callable

from sqlalchemy import Connection, Table
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session
from sqlalchemy.sql.dml import Insert

_INSERTS: dict[str, Callable[[Table], Insert]] = {
    "postgresql": postgresql.insert,
    "sqlite": sqlite.insert,
}


def insert_for(bind: Session | Connection) -> Callable[[Table], Insert]:
    """The `insert` construct for the dialect `bind` talks to."""
    engine_or_conn = bind.get_bind() if isinstance(bind, Session) else bind
    name = engine_or_conn.dialect.name
    try:
        return _INSERTS[name]
    except KeyError:
        raise NotImplementedError(f"no upsert insert for dialect {name!r}") from None
