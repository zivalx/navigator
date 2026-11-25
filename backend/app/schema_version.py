"""Where Alembic lives and which revision the code and the database are at."""
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine

ALEMBIC_INI: Path = Path(__file__).resolve().parent.parent / "alembic.ini"


def alembic_config(engine: Engine | None = None, *, lock_held: bool = False) -> Config:
    """Config for programmatic use. `engine` overrides settings.database_url in
    env.py; `lock_held` tells env.py the caller already holds the migration lock."""
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes["engine"] = engine
    cfg.attributes["lock_held"] = lock_held
    return cfg


def head_revision() -> str:
    """The single head revision shipped with this code."""
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def db_revision(conn: Connection) -> str | None:
    """Revision stamped in the database, or None when alembic_version is absent."""
    return MigrationContext.configure(conn).get_current_revision()
