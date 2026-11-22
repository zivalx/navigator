"""Alembic migrations against a real Postgres (opt-in: TEST_POSTGRES_URL)."""
import threading
from datetime import datetime, timezone

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import event, inspect

import app.models  # noqa: F401  (registers every table on Base.metadata)
from app import database
from app.database import Base
from app.models.asset import AssetType, Currency, MarketRegion
from app.schema_version import alembic_config, db_revision, head_revision
from tests.pg_helpers import FIXTURES, enum_labels, load_sql, requires_pg, scratch_schema


def _diffs(conn):
    return compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)


def _use_engine(monkeypatch, eng):
    """Point app.database at the scratch schema (engine and sessions), so nothing
    under test can reach the database settings.database_url names."""
    monkeypatch.setattr(database, "engine", eng)
    monkeypatch.setattr(database, "SessionLocal",
                        database.sessionmaker(autocommit=False, autoflush=False, bind=eng))


def _marker_times(conn):
    return [r[0] for r in conn.exec_driver_sql(
        "SELECT ran_at FROM maintenance_runs WHERE name='minor_units_cutover'")]


@requires_pg
def test_pg_upgrade_empty_matches_models():
    with scratch_schema() as eng:
        command.upgrade(alembic_config(eng), "head")
        with eng.connect() as c:
            assert _diffs(c) == []
            assert db_revision(c) == head_revision()
            assert enum_labels(c, "currency") == [m.name for m in Currency]          # 18, model order
            assert enum_labels(c, "assettype") == ["STOCK", "ETF", "CRYPTO", "FUND", "INDEX", "OTHER"]
            assert enum_labels(c, "marketregion") == ["US", "EU", "ASIA", "IL"]
            assert enum_labels(c, "alertrule") == ["PRICE_BELOW", "PRICE_ABOVE", "TRAILING_STOP"]
            assert enum_labels(c, "alertintent") == ["BUY", "SELL"]
            assert c.exec_driver_sql("SELECT count(*) FROM maintenance_runs WHERE name='minor_units_cutover'").scalar() == 1


@requires_pg
@pytest.mark.parametrize("fixture", ["legacy_pre_tase.sql", "legacy_tase.sql"])
def test_pg_adopt_legacy(fixture):
    from app import migrate

    with scratch_schema() as eng:
        load_sql(eng, FIXTURES / fixture)
        before, after = migrate.run_migrations(eng)
        assert before is None and after == head_revision()
        with eng.connect() as c:
            assert db_revision(c) == head_revision()
            assert _diffs(c) == []
            # Adopted DBs keep their label order; the label *sets* must match the models.
            assert set(enum_labels(c, "currency")) == {m.name for m in Currency}
            assert set(enum_labels(c, "assettype")) == {m.name for m in AssetType}
            assert set(enum_labels(c, "marketregion")) == {m.name for m in MarketRegion}
            assert set(enum_labels(c, "alertrule")) == {"PRICE_BELOW", "PRICE_ABOVE", "TRAILING_STOP"}
            assert set(enum_labels(c, "alertintent")) == {"BUY", "SELL"}
            times = _marker_times(c)
        assert len(times) == 1
        if fixture == "legacy_tase.sql":
            assert times[0] == datetime(2026, 11, 17, 8, 0, tzinfo=timezone.utc)


@requires_pg
def test_pg_adopt_refuses_partial(monkeypatch, capsys):
    from app import migrate

    with scratch_schema() as eng:
        load_sql(eng, FIXTURES / "legacy_tase.sql")
        with eng.begin() as c:
            c.exec_driver_sql("ALTER TABLE price_alerts DROP COLUMN acknowledged_at")
        _use_engine(monkeypatch, eng)
        assert migrate.main() == 1
        out, err = capsys.readouterr()
        assert out == ""
        assert "acknowledged_at" in err
        with eng.connect() as c:
            assert db_revision(c) is None
            assert not inspect(c).has_table("alembic_version")


@requires_pg
def test_pg_migrate_concurrent():
    from app import migrate

    with scratch_schema() as eng:
        barrier = threading.Barrier(2)
        errors, results = [], []

        def run():
            try:
                barrier.wait(timeout=10)
                results.append(migrate.run_migrations(eng))
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=run) for _ in range(2)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert not errors, errors
        head = head_revision()
        assert sorted(results, key=str) == sorted([(None, head), (head, head)], key=str)
        with eng.connect() as c:
            assert c.exec_driver_sql("SELECT count(*) FROM alembic_version").scalar() == 1
            assert len(_marker_times(c)) == 1


@requires_pg
def test_pg_init_db_raises_when_not_at_head(monkeypatch):
    with scratch_schema() as eng:
        command.upgrade(alembic_config(eng), "head")
        with eng.begin() as c:
            c.exec_driver_sql("UPDATE alembic_version SET version_num='0000'")
        _use_engine(monkeypatch, eng)
        with pytest.raises(RuntimeError, match="run python -m app.migrate") as exc:
            database.init_db()
        assert f"DB at 0000, code expects {head_revision()}" in str(exc.value)


@requires_pg
def test_pg_init_db_raises_on_unmigrated_db(monkeypatch):
    with scratch_schema() as eng:
        _use_engine(monkeypatch, eng)
        with pytest.raises(RuntimeError, match="DB at None.*run python -m app.migrate"):
            database.init_db()
        with eng.connect() as c:
            assert inspect(c).get_table_names() == []


@requires_pg
def test_pg_init_db_does_no_ddl_at_head(monkeypatch):
    with scratch_schema() as eng:
        command.upgrade(alembic_config(eng), "head")
        with eng.connect() as c:
            tables_before = sorted(inspect(c).get_table_names())
        statements = []
        event.listen(eng, "before_cursor_execute",
                     lambda conn, cursor, stmt, *a: statements.append(stmt))
        _use_engine(monkeypatch, eng)
        database.init_db()
        with eng.connect() as c:
            assert sorted(inspect(c).get_table_names()) == tables_before
        writes = [s for s in statements
                  if s.lstrip().split(None, 1)[0].upper() in {"CREATE", "ALTER", "DROP", "INSERT", "UPDATE", "DELETE"}]
        assert writes == []


@requires_pg
def test_pg_migrate_main_prints_only_revisions(monkeypatch, capsys):
    from app import migrate

    with scratch_schema() as eng:
        _use_engine(monkeypatch, eng)
        assert migrate.main() == 0
        assert capsys.readouterr().out.strip().splitlines() == [f"schema: none -> {head_revision()}"]
        # A second run is a no-op that still reports where it is.
        assert migrate.main() == 0
        assert capsys.readouterr().out.strip().splitlines() == [f"schema: {head_revision()} -> {head_revision()}"]


def test_legacy_schema_constants_match_models():
    """The adopt path is valid only while the baseline models are unchanged
    (runs in the default suite; no Postgres needed)."""
    from app import legacy_schema
    from app.models.alert import AlertIntent, AlertRule, PriceAlert

    assert set(legacy_schema.BASELINE_TABLES) == set(Base.metadata.tables)
    assert legacy_schema.EXPECTED_ENUM_LABELS == {
        "currency": frozenset(m.name for m in Currency),
        "assettype": frozenset(m.name for m in AssetType),
        "marketregion": frozenset(m.name for m in MarketRegion),
        "alertrule": frozenset(m.name for m in AlertRule),
        "alertintent": frozenset(m.name for m in AlertIntent),
    }
    assert legacy_schema.REQUIRED_PRICE_ALERT_COLUMNS == {c.name for c in PriceAlert.__table__.columns}
