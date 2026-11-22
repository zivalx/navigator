"""Alembic migrations against a real Postgres (opt-in: TEST_POSTGRES_URL)."""
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

import app.models  # noqa: F401  (registers every table on Base.metadata)
from app.database import Base
from app.models.asset import Currency
from app.schema_version import alembic_config, db_revision, head_revision
from tests.pg_helpers import enum_labels, requires_pg, scratch_schema


@requires_pg
def test_pg_upgrade_empty_matches_models():
    with scratch_schema() as eng:
        command.upgrade(alembic_config(eng), "head")
        with eng.connect() as c:
            assert compare_metadata(MigrationContext.configure(c, opts={"compare_type": True}), Base.metadata) == []
            assert db_revision(c) == head_revision()
            assert enum_labels(c, "currency") == [m.name for m in Currency]          # 18, model order
            assert enum_labels(c, "assettype") == ["STOCK", "ETF", "CRYPTO", "FUND", "INDEX", "OTHER"]
            assert enum_labels(c, "marketregion") == ["US", "EU", "ASIA", "IL"]
            assert enum_labels(c, "alertrule") == ["PRICE_BELOW", "PRICE_ABOVE", "TRAILING_STOP"]
            assert enum_labels(c, "alertintent") == ["BUY", "SELL"]
            assert c.exec_driver_sql("SELECT count(*) FROM maintenance_runs WHERE name='minor_units_cutover'").scalar() == 1
