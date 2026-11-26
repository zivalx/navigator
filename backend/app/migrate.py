"""`python -m app.migrate`: bring the database to the code's Alembic head.

Runs once per container start (docker-entrypoint.sh), before the app forks its
workers, under MIGRATION_LOCK_KEY so concurrent runs serialise:
- empty schema → upgrade head;
- legacy schema (tables, no alembic_version) → converge, verify, stamp 0001,
  upgrade head; refuses (exit 1, nothing stamped) if verification fails;
- alembic_version present → upgrade head.
Prints the revision before and after, and nothing else.
"""
import sys

from alembic import command
from sqlalchemy import Engine, inspect

from . import database, legacy_schema
from .locks import MIGRATION_LOCK_KEY, advisory_lock
from .schema_version import alembic_config, db_revision


class AdoptionError(RuntimeError):
    """A legacy database did not converge to the baseline; it was not stamped."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def run_migrations(engine: Engine | None = None) -> tuple[str | None, str | None]:
    """Migrate to head; returns (revision before, revision after)."""
    if engine is None:
        engine = database.engine
    cfg = alembic_config(engine, lock_held=True)
    with advisory_lock(engine, MIGRATION_LOCK_KEY) as conn:
        before = db_revision(conn)
        insp = inspect(conn)
        if not insp.has_table("alembic_version") and insp.has_table("assets"):
            legacy_schema.converge(engine)
            problems = legacy_schema.verify(engine)
            if problems:
                raise AdoptionError(problems)
            command.stamp(cfg, "0001")
        command.upgrade(cfg, "head")
        after = db_revision(conn)
    return before, after


def main() -> int:
    # APP_ENV=development turns on SQL echo to stdout; keep stdout to the one line.
    database.engine.echo = False
    try:
        before, after = run_migrations()
    except AdoptionError as e:
        print("legacy database not adopted (nothing stamped):", file=sys.stderr)
        for problem in e.problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print(f"schema: {before or 'none'} -> {after}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
